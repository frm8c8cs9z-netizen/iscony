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
    const zoomControls = byId("public-schedule-zoom");
    const zoomKey = "public-schedule-table-zoom";
    const zoomSupported = window.CSS && CSS.supports("zoom", "1");
    const zoomTables = [...document.querySelectorAll("#public-schedule-tables .schedule-table-wrapper")]
        .map((wrapper) => ({wrapper, table: wrapper.querySelector("table"), pinch: null}));
    let zoom = 1;
    try {
        const saved = Number(localStorage.getItem(zoomKey));
        if (Number.isFinite(saved) && saved > 0) zoom = saved;
    } catch (_) { zoom = 1; }
    const zoomActive = () => zoomSupported && media.matches && mode === "table" && zoomTables.length > 0;
    function fitZoom() {
        // 表幅はzoomを除いた値。全日程区分が同じ倍率で収まるよう最も広い表に合わせる。
        return Math.max(0.2, Math.min(1, ...zoomTables.map(({wrapper, table}) =>
            (wrapper.clientWidth - 1) / (table.getBoundingClientRect().width / (Number(table.style.zoom) || 1)))));
    }
    function applyZoom(value, anchor = null) {
        if (!zoomActive()) return;
        const next = Math.max(fitZoom(), Math.min(1.5, value));
        zoomTables.forEach(({wrapper, table}) => {
            const previous = Number(table.style.zoom) || 1;
            const x = anchor && anchor.wrapper === wrapper ? anchor.x : wrapper.clientWidth / 2;
            const y = anchor && anchor.wrapper === wrapper ? anchor.y : wrapper.clientHeight / 2;
            const left = (wrapper.scrollLeft + x) * next / previous - x;
            const top = (wrapper.scrollTop + y) * next / previous - y;
            table.style.zoom = String(next);
            wrapper.scrollLeft = left;
            wrapper.scrollTop = top;
        });
        zoom = next;
        byId("public-schedule-zoom-value").textContent = `${Math.round(zoom * 100)}%`;
        byId("public-schedule-zoom-out").disabled = zoom <= fitZoom() + 0.001;
        byId("public-schedule-zoom-in").disabled = zoom >= 1.5;
    }
    function saveZoom() {
        try { localStorage.setItem(zoomKey, String(zoom)); } catch (_) { applyZoom(1); }
    }
    function renderZoom() {
        const active = zoomActive();
        document.body.classList.toggle("public-schedule-table-zoom", active);
        zoomControls.hidden = !active;
        zoomTables.forEach((item) => {
            item.pinch = null;
            if (!active) item.table.style.removeProperty("zoom");
        });
        if (active) applyZoom(zoom);
    }
    [["out", 0.8], ["in", 1.25], ["fit", null]].forEach(([action, factor]) => {
        byId(`public-schedule-zoom-${action}`).addEventListener("click", () => {
            applyZoom(factor === null ? fitZoom() : zoom * factor);
            saveZoom();
        });
    });
    zoomTables.forEach((item) => {
        const {wrapper} = item;
        const distance = (touches) => Math.hypot(touches[1].clientX - touches[0].clientX,
            touches[1].clientY - touches[0].clientY);
        wrapper.addEventListener("touchstart", (event) => {
            if (!zoomActive() || event.touches.length !== 2) { item.pinch = null; return; }
            event.preventDefault();
            const rect = wrapper.getBoundingClientRect();
            item.pinch = {distance: distance(event.touches), zoom,
                anchor: {wrapper, x: (event.touches[0].clientX + event.touches[1].clientX) / 2 - rect.left,
                    y: (event.touches[0].clientY + event.touches[1].clientY) / 2 - rect.top}};
        }, {passive: false});
        wrapper.addEventListener("touchmove", (event) => {
            if (!zoomActive() || event.touches.length !== 2 || !item.pinch) return;
            event.preventDefault();
            if (item.pinch.distance > 0) {
                applyZoom(item.pinch.zoom * distance(event.touches) / item.pinch.distance, item.pinch.anchor);
            }
        }, {passive: false});
        const endPinch = () => {
            if (item.pinch) { item.pinch = null; saveZoom(); }
        };
        wrapper.addEventListener("touchend", endPinch);
        wrapper.addEventListener("touchcancel", endPinch);
        ["gesturestart", "gesturechange"].forEach((name) => wrapper.addEventListener(name, (event) => {
            if (zoomActive()) event.preventDefault();
        }, {passive: false}));
    });
    window.addEventListener("resize", () => { if (zoomActive()) applyZoom(zoom); });
    const cleanText = (node) => node ? node.textContent.replace(/\s+/g, " ").trim() : "";
    const textList = (cell, selector, separator = " / ") => [...cell.querySelectorAll(selector)].map(cleanText).join(separator);
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
    const searchPanel = byId("public-schedule-search");
    const searchResults = byId("public-schedule-search-results");
    const queryInput = byId("public-schedule-query");
    const categoryInput = byId("public-schedule-category");
    const searchKey = "public-schedule-search";
    const searchData = JSON.parse(byId("public-schedule-search-data").textContent);
    const normalize = (value) => String(value).normalize("NFKC").toLowerCase()
        .replace(/[ぁ-ゖ]/g, (char) => String.fromCharCode(char.charCodeAt(0) + 0x60))
        .replace(/[\s・･··•‧∙⋅●･]/g, "");
    const entries = Object.fromEntries(Object.entries(searchData.entries).map(([id, entry]) =>
        [id, {category: entry[0], code: normalize(entry[1]), texts: entry[2].map(normalize)}]));
    Object.entries(searchData.categories).forEach(([id, name]) => {
        const option = document.createElement("option");
        option.value = id;
        option.textContent = name;
        categoryInput.appendChild(option);
    });
    try {
        const saved = JSON.parse(localStorage.getItem(searchKey) || "null");
        if (saved && typeof saved.query === "string" && typeof saved.category === "string") {
            queryInput.value = saved.query;
            categoryInput.value = saved.category;
        }
    } catch (_) { queryInput.value = ""; categoryInput.value = ""; }
    const searchMatches = [];
    blocks.forEach((block, blockIndex) => block.courts.forEach((court, courtIndex) => {
        let pending = 0;
        court.cells.forEach((cell) => {
            if (cell.querySelector("[data-ghost]")) return;
            const done = cell.dataset.resultEntered === "true";
            searchMatches.push({cell, block, court, blockIndex, courtIndex, done,
                ahead: done ? null : pending++, ids: (cell.dataset.searchEntries || "").split(" ")});
        });
    }));
    searchMatches.sort((a, b) => a.blockIndex - b.blockIndex
        || Number(a.cell.dataset.order) - Number(b.cell.dataset.order) || a.courtIndex - b.courtIndex);
    function renderSearch() {
        const query = normalize(queryInput.value);
        const list = byId("public-schedule-search-list");
        list.replaceChildren();
        if (!query) return false;
        const numeric = /^[0-9]+$/.test(query);
        const found = searchMatches.filter((match) => match.ids.some((id) => {
            const entry = entries[id];
            return entry && (!categoryInput.value || entry.category === categoryInput.value)
                && (numeric ? entry.code === query : entry.texts.some((text) => text.includes(query)));
        }));
        byId("public-schedule-search-count").textContent = `「${queryInput.value}」の試合 ${found.length}件`;
        if (!found.length) list.appendChild(element("p", "", "見つかりませんでした"));
        const completed = element("details", "public-search-completed");
        completed.appendChild(element("summary", "", `終わった試合(${found.filter((item) => item.done).length})`));
        found.forEach(({cell, block, court, done, ahead}) => {
            const button = element("button", "public-search-match");
            button.type = "button";
            button.dataset.scheduleTarget = cell.id;
            const heading = element("span", "public-court-match-heading",
                `${blocks.length > 1 ? block.name + " / " : ""}${courtName(court)} 第${cell.dataset.order}試合`);
            if (!done) heading.appendChild(ahead < 2 ? progressBadge(ahead === 0 ? "now" : "next")
                : element("span", "public-search-ahead", `あと${ahead}試合`));
            button.append(heading,
                element("span", "public-court-pairs", textList(cell, ".schedule-entry-name", " vs ") || "未確定"),
                element("span", "public-court-context", textList(cell, ".schedule-context")));
            if (done) button.appendChild(element("span", "public-search-score", score(cell)));
            button.addEventListener("click", () => navigate(`#${cell.id}`));
            (done ? completed : list).appendChild(button);
        });
        if (completed.children.length > 1) list.appendChild(completed);
        return true;
    }
    function searchChanged() {
        try {
            localStorage.setItem(searchKey, JSON.stringify({query: queryInput.value, category: categoryInput.value}));
        } catch (_) { /* Search remains usable without storage. */ }
        if (window.location.hash !== "#schedule-overview") navigate("#schedule-overview");
        else render();
    }
    queryInput.addEventListener("input", searchChanged);
    categoryInput.addEventListener("change", searchChanged);
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
        renderZoom();
        if (!mobile) return;
        let selected;
        for (const block of blocks) {
            // カテゴリ結果画面の既存の「進行表へ戻る」(#schedule-ID)も受け付ける。
            const court = block.courts.find((court) => route(block, court) === window.location.hash
                || court.cells.some((cell) => `#${cell.id}` === window.location.hash));
            if (court) selected = {block, court};
        }
        searchPanel.hidden = Boolean(selected);
        const searching = !selected && renderSearch();
        searchResults.hidden = !searching;
        overview.hidden = Boolean(selected) || searching;
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
            if (`#${cell.id}` === window.location.hash) {
                anchored = item;
                if (completed) item.open = true;
                item.classList.add("public-search-highlight");
                window.setTimeout(() => item.classList.remove("public-search-highlight"), 3000);
            }
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
        requestAnimationFrame(() => searchPanel.scrollIntoView({block: "start"}));
    });
    window.addEventListener("hashchange", render);
    media.addEventListener("change", render);
    document.body.classList.add("public-schedule-enhanced");
    controls.hidden = false;
    render();
})();
