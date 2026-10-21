/* Read-only print layout: one DOM page per physical sheet. */
(function () {
    "use strict";
    const pages = document.getElementById("print-pages");
    const sources = document.getElementById("print-sources");
    const controls = {};
    ["paper", "orientation", "columns", "content", "winner-placement", "repeat-header"].forEach(key => {
        controls[key] = document.getElementById("print-" + key);
        if (controls[key]) controls[key].addEventListener("change", update);
    });

    function page(title = "") {
        const node = document.getElementById("print-page-template").content.firstElementChild.cloneNode(true);
        node.querySelector(".print-bracket-title").textContent = title;
        node.querySelector(".print-header").hidden = pages.children.length > 0 && !controls["repeat-header"].checked;
        // Reserve the number's height before measuring the body.
        const bound = Math.max(1, sources.querySelectorAll(".league-print-group, .print-diagram").length);
        node.querySelector(".print-page-number").textContent = bound + "/" + bound;
        pages.append(node);
        return node.querySelector(".print-page-body");
    }

    function league() {
        const grid = sources.querySelector('[data-content="' + controls.content.value + '"]');
        let body = page();
        const groups = Array.from(grid.querySelectorAll(".league-print-group"), group => group.cloneNode(true));
        if (!groups.length) {
            body.textContent = grid.textContent.trim();
            return;
        }
        const columns = Number(controls.columns.value);
        const rows = [];
        for (let i = 0; i < groups.length; i += columns) {
            const row = document.createElement("div");
            row.className = "print-league-row";
            row.style.setProperty("--columns", columns);
            row.append(...groups.slice(i, i + columns));
            rows.push(row);
            body.append(row);
        }
        const bodyHeight = body.clientHeight;
        // Preserve the Stage-wide font size derived from intrinsic table widths.
        let size = 12;
        groups.forEach(group => { group.style.fontSize = size + "px"; });
        let ratio = Infinity;
        groups.forEach(group => {
            const style = getComputedStyle(group);
            const width = group.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
            group.querySelectorAll(".round-robin-table").forEach(table => {
                ratio = Math.min(ratio, width / table.getBoundingClientRect().width);
            });
        });
        if (Number.isFinite(ratio)) size = Math.max(2, Math.min(28, size * ratio));
        for (let pass = 0; pass < 12; pass++) {
            groups.forEach(group => { group.style.fontSize = size + "px"; });
            let correction = 1;
            groups.forEach(group => {
                const style = getComputedStyle(group);
                const width = group.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
                group.querySelectorAll("table").forEach(table => {
                    correction = Math.min(correction, width / table.getBoundingClientRect().width);
                });
                correction = Math.min(correction, bodyHeight / group.getBoundingClientRect().height);
            });
            if (correction >= 0.999) break;
            size *= correction * 0.99;
        }
        groups.forEach(group => { group.style.fontSize = size + "px"; });
        rows.forEach(row => row.remove());
        let used = 0;
        rows.forEach(row => {
            body.append(row);
            const height = row.getBoundingClientRect().height;
            const gap = parseFloat(getComputedStyle(row).marginBottom);
            if (used > 0 && used + height > body.clientHeight) {
                body = page();
                body.append(row);
                used = 0;
            }
            used += height + gap;
        });
    }

    function tournament() {
        const brackets = sources.querySelectorAll("[data-bracket-name]");
        if (!brackets.length) {
            page().textContent = "トーナメント表がありません。";
            return;
        }
        brackets.forEach(bracket => {
            const diagrams = Array.from(bracket.querySelectorAll(".print-diagram"));
            const plans = [];
            diagrams.forEach(diagram => {
                if (diagram.dataset.splitRole === "winner" && controls["winner-placement"].value === "together" && plans.length) {
                    plans[plans.length - 1].push(diagram);
                } else plans.push([diagram]);
            });
            if (!plans.length) {
                page(bracket.dataset.bracketName).textContent = bracket.textContent.trim();
                return;
            }
            let scale = Infinity;
            const rendered = [];
            plans.forEach(plan => {
                const title = [bracket.dataset.bracketName, ...plan.map(diagram => diagram.dataset.title)].filter(Boolean).join(" / ");
                const body = page(title);
                body.classList.add("print-tournament-body");
                const stack = document.createElement("div");
                stack.className = "print-diagrams";
                body.append(stack);
                let height = 0;
                let width = 0;
                let headingsHeight = 0;
                plan.forEach(source => {
                    const diagram = source.cloneNode(true);
                    const heading = diagram.querySelector("h3");
                    if (heading) heading.hidden = plan.length === 1;
                    stack.append(diagram);
                    const svg = diagram.querySelector("svg");
                    const box = svg.viewBox.baseVal;
                    width = Math.max(width, box.width);
                    height += box.height;
                    if (heading && !heading.hidden) {
                        headingsHeight += heading.getBoundingClientRect().height + parseFloat(getComputedStyle(heading).marginBottom);
                    }
                    rendered.push({svg, width: box.width, height: box.height});
                });
                const gap = parseFloat(getComputedStyle(stack).rowGap) * (plan.length - 1);
                scale = Math.min(scale, body.clientWidth / width, (body.clientHeight - headingsHeight - gap) / height);
            });
            // A single common scale for every block and winner page of this bracket.
            rendered.forEach(({svg, width, height}) => {
                svg.setAttribute("width", width * scale);
                svg.setAttribute("height", height * scale);
                svg.style.width = width * scale + "px";
                svg.style.height = height * scale + "px";
            });
        });
    }

    function update() {
        const paper = controls.paper.value;
        const orientation = controls.orientation.value;
        const dimensions = paper === "A3" ? [281, 404] : [194, 281];
        if (orientation === "landscape") dimensions.reverse();
        pages.style.setProperty("--page-width", dimensions[0] + "mm");
        pages.style.setProperty("--page-height", dimensions[1] + "mm");
        document.getElementById("stage-print-page").textContent = "@page { size: " + paper + " " + orientation + "; margin: 8mm; }";
        pages.replaceChildren();
        if (controls.columns) league(); else tournament();
        const total = pages.children.length;
        Array.from(pages.children).forEach((node, index) => {
            node.querySelector(".print-page-number").textContent = (index + 1) + "/" + total;
        });
    }
    document.getElementById("print-button").addEventListener("click", () => window.print());
    window.addEventListener("beforeprint", update);
    window.addEventListener("afterprint", update);
    window.addEventListener("resize", update);
    if (document.fonts) document.fonts.ready.then(update);
    update();
})();
