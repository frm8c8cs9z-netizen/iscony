(() => {
    "use strict";
    const byId = (id) => document.getElementById(id);
    const controls = byId("public-schedule-controls");
    if (!controls) return;
    const media = window.matchMedia("(max-width: 640px)");
    const overview = byId("public-schedule-overview");
    const courtView = byId("public-schedule-court");
    const matches = byId("public-schedule-matches");
    const key = "public-schedule-display";
    let mode = "mobile";
    try { mode = localStorage.getItem(key) === "table" ? "table" : "mobile"; } catch (_) { mode = "mobile"; }
    let restored = [];
    const cleanText = (node) => node ? node.textContent.replace(/\s+/g, " ").trim() : "";
    const textList = (cell, selector) => [...cell.querySelectorAll(selector)].map(cleanText).join(" / ");
    const element = (tag, className, text) => {
        const node = document.createElement(tag);
        node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    };
    const courtName = (court) => `${court.name}コート`;
    const progressBadge = (status) => element("span",
        `public-progress-badge${status === "next" ? " public-progress-badge-next" : ""}`,
        status === "now" ? "今" : "次");
    const score = (cell) => [...cell.querySelectorAll(".schedule-pair-line strong, .schedule-score-line strong")]
        .map(cleanText).join(" - ");
    const blocks = [...document.querySelectorAll("[data-schedule-block]")].map((section) => ({
        id: section.dataset.scheduleBlock, name: section.dataset.blockName,
        initialOpen: section.dataset.initialOpen === "true",
        courts: [...section.querySelectorAll("thead [data-court-id]")].map((header) => ({
            id: header.dataset.courtId, name: cleanText(header),
            cells: [...section.querySelectorAll("tbody td[data-court-id]")].filter((cell) => cell.dataset.courtId === header.dataset.courtId),
        })),
    }));
    function restoreCards() {
        restored.forEach(({cell, nodes}) => nodes.forEach((node) => cell.appendChild(node)));
        restored = [];
        matches.replaceChildren();
    }
    function route(block, court) { return `#court-${block.id}-${court.id}`; }
    function navigate(hash) {
        if (window.location.hash === hash) render();
        else window.location.hash = hash;
    }
    blocks.forEach((block) => {
        const details = document.createElement("details");
        details.open = block.initialOpen;
        details.dataset.mobileBlock = block.id;
        const heading = document.createElement("summary");
        heading.textContent = block.name;
        details.appendChild(heading);
        block.courts.forEach((court) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "public-court-summary";
            button.dataset.courtTarget = route(block, court);
            button.appendChild(element("span", "public-court-name", courtName(court)));
            const chevron = element("span", "public-court-chevron", "›");
            chevron.setAttribute("aria-hidden", "true");
            button.appendChild(chevron);
            ["now", "next"].forEach((status) => {
                const cell = court.cells.find((cell) => cell.dataset.progress === status);
                if (!cell) {
                    if (status === "now") button.appendChild(element("span", "public-court-finished", "全試合終了"));
                    return;
                }
                const line = element("span", "public-court-progress");
                const heading = element("span", "public-court-match-heading");
                heading.append(progressBadge(status), document.createTextNode(`第${cell.dataset.order}試合`));
                line.appendChild(heading);
                const pairs = element("span", "public-court-pairs");
                cell.querySelectorAll(".schedule-pair-main").forEach((pair, index) => {
                    if (index) pairs.appendChild(element("span", "public-court-vs", "vs"));
                    pairs.appendChild(element("span", "public-court-pair",
                        cleanText(pair.querySelector(".schedule-entry-name") || pair)));
                });
                line.append(pairs, element("span", "public-court-context", textList(cell, ".schedule-context")));
                button.appendChild(line);
            });
            button.addEventListener("click", () => navigate(route(block, court)));
            details.appendChild(button);
        });
        byId("public-schedule-blocks").appendChild(details);
    });
    function render() {
        restoreCards();
        const mobile = media.matches && mode === "mobile";
        document.body.classList.toggle("public-schedule-mobile", mobile);
        byId("public-schedule-mobile").hidden = !mobile;
        byId("public-schedule-show-table").hidden = mode === "table";
        byId("public-schedule-show-mobile").hidden = mode !== "table";
        if (!mobile) return;
        let selected;
        for (const block of blocks) {
            // カテゴリ結果画面の既存の「進行表へ戻る」(#schedule-ID)も受け付ける。
            const court = block.courts.find((court) => route(block, court) === window.location.hash
                || court.cells.some((cell) => `#${cell.id}` === window.location.hash));
            if (court) selected = {block, court};
        }
        overview.hidden = Boolean(selected);
        courtView.hidden = !selected;
        if (!selected) return;
        const {block, court} = selected;
        const title = byId("public-schedule-court-title");
        title.textContent = `${block.name} / ${courtName(court)}`;
        const nav = byId("public-schedule-courts");
        nav.replaceChildren();
        block.courts.forEach((other) => {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = courtName(other);
            button.setAttribute("aria-current", String(other === court));
            button.addEventListener("click", () => navigate(route(block, other)));
            nav.appendChild(button);
        });
        let current = null;
        let anchored = null;
        court.cells.forEach((cell) => {
            const completed = cell.dataset.resultEntered === "true" && !cell.querySelector("[data-ghost]");
            const item = document.createElement(completed ? "details" : "article");
            item.className = "public-mobile-match";
            item.dataset.progress = cell.dataset.progress;
            const label = document.createElement(completed ? "summary" : "div");
            label.textContent = `第${cell.dataset.order}試合` + (completed ? ` ${score(cell)} ${textList(cell, ".schedule-context")}` : "");
            if (cell.dataset.progress) {
                label.appendChild(progressBadge(cell.dataset.progress));
            }
            item.appendChild(label);
            const nodes = [...cell.childNodes];
            restored.push({cell, nodes});
            nodes.forEach((node) => item.appendChild(node));
            matches.appendChild(item);
            if (cell.dataset.progress === "now") current = item;
            if (`#${cell.id}` === window.location.hash) anchored = item;
        });
        title.focus({preventScroll: true});
        const destination = anchored || current || title;
        window.requestAnimationFrame(() => {
            if (destination.isConnected && document.body.classList.contains("public-schedule-mobile")) {
                destination.scrollIntoView({block: "start"});
            }
        });
    }
    function chooseMode(value) {
        mode = value;
        try { localStorage.setItem(key, value); } catch (_) { mode = "mobile"; }
        render();
    }
    byId("public-schedule-show-table").addEventListener("click", () => chooseMode("table"));
    byId("public-schedule-show-mobile").addEventListener("click", () => chooseMode("mobile"));
    byId("public-schedule-back").addEventListener("click", () => {
        navigate("#schedule-overview");
        requestAnimationFrame(() => overview.scrollIntoView({block: "start"}));
    });
    window.addEventListener("hashchange", render);
    media.addEventListener("change", render);
    document.body.classList.add("public-schedule-enhanced");
    controls.hidden = false;
    render();
})();
