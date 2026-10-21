/* Read-only print layout: one DOM page per physical sheet. */
(function () {
    "use strict";
    const pages = document.getElementById("print-pages");
    const sources = document.getElementById("print-sources");
    const controls = {};
    ["paper", "orientation", "columns", "content", "winner-placement", "repeat-header", "frame"].forEach(key => {
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
        // Measure every page unit against the first (fully headed) page.
        // Later pages can have more space when their header is hidden.
        const probe = page();
        probe.classList.add("print-tournament-body");
        const bodyWidth = probe.clientWidth;
        const bodyHeight = probe.clientHeight;
        const units = [];
        brackets.forEach(bracket => {
            const plans = [];
            bracket.querySelectorAll(".print-diagram").forEach(diagram => {
                if (diagram.dataset.splitRole === "winner" && controls["winner-placement"].value === "together" && plans.length) {
                    plans[plans.length - 1].push(diagram);
                } else plans.push([diagram]);
            });
            if (!plans.length) {
                const stack = document.createElement("div");
                stack.className = "print-diagrams";
                stack.dataset.pageUnit = "packable";
                const heading = document.createElement("h3");
                heading.textContent = bracket.dataset.bracketName;
                heading.style.maxWidth = bodyWidth + "px";
                const message = document.createElement("p");
                message.textContent = bracket.textContent.trim();
                const diagram = document.createElement("div");
                diagram.className = "print-diagram";
                diagram.append(heading, message);
                stack.append(diagram);
                units.push({stack, isolated: false, svgs: [], scale: Infinity});
            }
            plans.forEach(plan => {
                const stack = document.createElement("div");
                stack.className = "print-diagrams";
                const isolated = plan.some(source => source.dataset.splitRole === "block");
                stack.dataset.pageUnit = isolated ? "split-block" : "packable";
                probe.append(stack);
                let width = 0;
                let height = 0;
                let headingsHeight = 0;
                const svgs = [];
                plan.forEach(source => {
                    const diagram = source.cloneNode(true);
                    stack.append(diagram);
                    const heading = diagram.querySelector("h3");
                    heading.style.maxWidth = bodyWidth + "px";
                    const svg = diagram.querySelector("svg");
                    const box = svg.viewBox.baseVal;
                    width = Math.max(width, box.width);
                    height += box.height;
                    headingsHeight += heading.getBoundingClientRect().height + parseFloat(getComputedStyle(heading).marginBottom);
                    svgs.push({svg, width: box.width, height: box.height});
                });
                const gap = parseFloat(getComputedStyle(stack).rowGap) * (plan.length - 1);
                units.push({stack, isolated, svgs, scale: Math.min(bodyWidth / width, (bodyHeight - headingsHeight - gap) / height)});
                stack.remove();
            });
        });
        // Use the page count at native size (or the fit limit when smaller)
        // as the budget, then enlarge all units together within that budget.
        const fitScale = Math.min(...units.map(unit => unit.scale));
        function layout(scale) {
            units.forEach(unit => unit.svgs.forEach(({svg, width, height}) => {
                svg.setAttribute("width", width * scale);
                svg.setAttribute("height", height * scale);
                svg.style.width = width * scale + "px";
                svg.style.height = height * scale + "px";
            }));
            pages.replaceChildren();
            let body = null;
            let shelf, rows;
            let gap = 0;
            function nextPage() {
                body = page();
                body.classList.add("print-tournament-body");
                shelf = document.createElement("div");
                shelf.className = "print-tournament-shelf";
                body.append(shelf);
                gap = parseFloat(getComputedStyle(shelf).rowGap);
                rows = [];
            }
            function place({stack, width, height}) {
                const usedHeight = rows.reduce((sum, row) => sum + row.height, 0) + gap * Math.max(0, rows.length - 1);
                const lastRow = rows[rows.length - 1];
                if (lastRow && lastRow.width + gap + width <= body.clientWidth + 0.5 &&
                    usedHeight + Math.max(0, height - lastRow.height) <= body.clientHeight + 0.5) {
                    lastRow.node.append(stack);
                    lastRow.width += gap + width;
                    lastRow.height = Math.max(lastRow.height, height);
                } else {
                    if (rows.length && usedHeight + gap + height > body.clientHeight + 0.5) nextPage();
                    const node = document.createElement("div");
                    node.className = "print-tournament-row";
                    shelf.append(node);
                    node.append(stack);
                    rows.push({node, width, height});
                }
            }
            // Preserve display order, filling only the last row before starting
            // a new row or page. Keep split blocks as forced page boundaries.
            let pending = [];
            function flush() {
                pending.forEach(unit => {
                    if (!body) nextPage();
                    place(unit);
                });
                pending = [];
            }
            units.forEach(({stack, isolated}) => {
                if (!body) nextPage();
                body.append(stack);
                const width = stack.getBoundingClientRect().width;
                const height = stack.getBoundingClientRect().height;
                stack.remove();
                if (isolated) {
                    flush();
                    // An empty measuring page can also serve as the block page.
                    if (rows.length) nextPage();
                    place({stack, width, height});
                    body = null;
                } else pending.push({stack, width, height});
            });
            flush();
            return pages.children.length;
        }
        let scale = Math.min(1, fitScale);
        const budget = layout(scale);
        if (Number.isFinite(fitScale) && fitScale > scale) {
            if (layout(fitScale) <= budget) {
                scale = fitScale;
            } else {
                let upper = fitScale;
                for (let pass = 0; pass < 24; pass++) {
                    const candidate = (scale + upper) / 2;
                    if (layout(candidate) <= budget) scale = candidate;
                    else upper = candidate;
                }
            }
            layout(scale);
        }
        if (!units.length) page().textContent = "対戦表がありません。";
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
        pages.classList.toggle("print-no-league-frame", !!controls.frame && !controls.frame.checked);
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
