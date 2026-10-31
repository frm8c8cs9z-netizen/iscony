/* Intrinsic column widths per Stage, body row heights per table. */
(function () {
    "use strict";
    const NAME_MAX_EM = 16;
    const ORGANIZATION_MAX_EM = 10;
    const ORGANIZATION_MIN_SCALE = 0.6;
    const ORGANIZATION_MAX_LINES = 2;
    const ORGANIZATION_FIT_PASSES = 16;

    function resetOrganization(cell) {
        ["font-size", "white-space", "overflow-wrap", "word-break", "line-height", "padding-block", "text-wrap", "inline-size"]
            .forEach(property => cell.style.removeProperty(property));
        cell.style.whiteSpace = "nowrap";
    }

    function fitOrganization(cell) {
        const baseSize = parseFloat(getComputedStyle(cell).fontSize);
        const shared = parseInt(getComputedStyle(cell).gridRowEnd.replace("span ", ""), 10) > 1;
        // Keep the original measured name row height, including layout rounding.
        const name = cell.parentElement.querySelector(".rr-player-name");
        const nameLineHeight = name.getBoundingClientRect().height;
        cell.style.lineHeight = nameLineHeight + "px";
        const range = document.createRange();
        range.selectNodeContents(cell);
        // Small kana and proportional glyphs can make the second line contain
        // more characters even with greedy wrapping. Require the midpoint on
        // the first line as well as fitting two lines; do not insert line breaks.
        const characters = [];
        const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) {
            const node = walker.currentNode;
            let offset = 0;
            for (const character of node.textContent) {
                characters.push({node, offset, length: character.length});
                offset += character.length;
            }
        }
        function characterRange(index) {
            const character = characters[index];
            if (!character) return null;
            const result = document.createRange();
            result.setStart(character.node, character.offset);
            result.setEnd(character.node, character.offset + character.length);
            return result;
        }
        const first = characterRange(0);
        const midpoint = characterRange(Math.ceil(characters.length / 2) - 1);
        function fits(scale, lines) {
            cell.style.fontSize = baseSize * scale + "px";
            const box = cell.getBoundingClientRect();
            const rects = Array.from(range.getClientRects());
            const height = rects.length ? Math.max(...rects.map(r => r.bottom)) -
                Math.min(...rects.map(r => r.top)) : 0;
            const firstLineLongEnough = lines === 1 || !first ||
                Math.abs(first.getBoundingClientRect().top - midpoint.getBoundingClientRect().top) < 0.01;
            return firstLineLongEnough && rects.every(r => r.left >= box.left - 0.01 && r.right <= box.right + 0.01) &&
                height <= Math.max(0, ...rects.map(r => r.height)) +
                    parseFloat(getComputedStyle(cell).lineHeight) * (lines - 1) + 0.01;
        }
        if (shared) {
            Object.assign(cell.style, {whiteSpace: "normal", overflowWrap: "anywhere", wordBreak: "normal"});
        }
        const lines = shared ? ORGANIZATION_MAX_LINES : 1;
        function fitTextWidth(scale) {
            // Keep the Stage column fixed; narrow only this text area. Ordinary
            // wrapping fills the first line first, including text inside red spans.
            if (fits(scale, 1)) return;
            let lower = 0;
            let upper = cell.getBoundingClientRect().width;
            for (let pass = 0; pass < ORGANIZATION_FIT_PASSES; pass++) {
                const width = (lower + upper) / 2;
                cell.style.inlineSize = width + "px";
                if (fits(scale, ORGANIZATION_MAX_LINES)) upper = width;
                else lower = width;
            }
            // Round up to a layout pixel fraction so quantization cannot add a line.
            cell.style.inlineSize = Math.ceil(upper * 64) / 64 + "px";
        }
        if (fits(1, lines)) {
            if (shared) fitTextWidth(1);
            return;
        }
        if (!shared && !fits(ORGANIZATION_MIN_SCALE, 1)) {
            // Two individual affiliation lines must fit within one name row.
            cell.style.lineHeight = nameLineHeight / 2 + "px";
            Object.assign(cell.style, {whiteSpace: "normal", overflowWrap: "anywhere", wordBreak: "normal"});
            if (fits(ORGANIZATION_MIN_SCALE, ORGANIZATION_MAX_LINES)) {
                fitTextWidth(ORGANIZATION_MIN_SCALE);
                return;
            }
        }
        // Shared affiliations use the full two-line area; individual affiliations
        // keep one line down to 60%, then wrap. Measure DOM text, including red spans.
        const wrapping = cell.style.whiteSpace === "normal";
        const fitLines = wrapping ? ORGANIZATION_MAX_LINES : lines;
        let lower = shared || wrapping ? 0 : ORGANIZATION_MIN_SCALE;
        let upper = !shared && wrapping ? ORGANIZATION_MIN_SCALE : 1;
        for (let pass = 0; pass < ORGANIZATION_FIT_PASSES; pass++) {
            const scale = (lower + upper) / 2;
            if (fits(scale, fitLines)) lower = scale;
            else upper = scale;
        }
        fits(lower, fitLines);
        if (wrapping) fitTextWidth(lower);
    }

    // Keep day-change markup while grouping text through each middle dot.
    // Inline blocks wrap as a unit; only an oversized unit wraps internally.
    function prepareName(line) {
        if (line.dataset.rrWords) return;
        const words = [];
        let word = document.createElement("span");
        function visit(node, ancestors) {
            if (node.nodeType === Node.TEXT_NODE) {
                node.textContent.split(/(?<=・)/u).forEach(text => {
                    if (!text) return;
                    let target = word;
                    ancestors.forEach(ancestor => {
                        const copy = ancestor.cloneNode(false);
                        target.append(copy);
                        target = copy;
                    });
                    target.append(document.createTextNode(text));
                    if (text.endsWith("・")) {
                        words.push(word);
                        word = document.createElement("span");
                    }
                });
            } else if (node.nodeType === Node.ELEMENT_NODE) {
                Array.from(node.childNodes).forEach(child => visit(child, [...ancestors, node]));
            }
        }
        Array.from(line.childNodes).forEach(node => visit(node, []));
        if (word.childNodes.length) words.push(word);
        line.replaceChildren();
        words.forEach(word => {
            word.className = "rr-name-word";
            Object.assign(word.style, {display: "inline-block", maxWidth: "100%",
                whiteSpace: "normal", overflowWrap: "anywhere", wordBreak: "normal"});
            line.append(word, document.createElement("wbr"));
        });
        Object.assign(line.style, {whiteSpace: "normal", wordBreak: "normal"});
        line.dataset.rrWords = "1";
    }

    function kind(cell) {
        if (cell.classList.contains("rr-name-col")) return "name";
        if (cell.classList.contains("rr-stat-col")) return "stat";
        return cell.cellIndex === 0 ? "code" : "score";
    }

    window.isconyAlignRoundRobinTables = function (root = document) {
        const tables = Array.from(root.querySelectorAll(".round-robin-table"));
        if (root.matches && root.matches(".round-robin-table")) tables.unshift(root);
        const stages = new Map();
        tables.forEach(table => {
            const box = table.getBoundingClientRect();
            if (!box.width || !box.height) return;
            const key = table.dataset.rrStage || table;
            if (!stages.has(key)) stages.set(key, []);
            stages.get(key).push(table);
        });
        stages.forEach(stageTables => {
            const widths = {code: 0, name: 0, score: 0, stat: 0};
            const playerWidths = {name: 0, organization: 0};
            // Reset all tables first, including dimensions copied by print layout.
            stageTables.forEach(table => {
                Object.assign(table.style, {tableLayout: "auto", width: "max-content",
                    minWidth: "0", maxWidth: "none"});
                Array.from(table.rows).forEach(row => {
                    row.style.removeProperty("height");
                    Array.from(row.cells).forEach(cell => {
                        Object.assign(cell.style, {width: "auto", minWidth: "0", maxWidth: "none",
                            height: "auto", whiteSpace: "nowrap", boxSizing: "border-box"});
                        if (kind(cell) === "name" && !cell.querySelector(".rr-player-rows")) {
                            cell.querySelectorAll(".rr-header-inner").forEach(line => {
                                prepareName(line);
                                line.style.whiteSpace = "nowrap";
                                line.querySelectorAll(".rr-name-word").forEach(word => {
                                    word.style.whiteSpace = "nowrap";
                                    word.style.maxWidth = "none";
                                });
                            });
                        }
                    });
                });
                table.querySelectorAll(".rr-player-rows").forEach(grid => {
                    grid.style.gridTemplateColumns = "max-content max-content";
                    grid.querySelectorAll(".rr-player-organization").forEach(resetOrganization);
                });
            });
            stageTables.forEach(table => {
                Object.keys(playerWidths).forEach(key => {
                    table.querySelectorAll(".rr-player-" + key).forEach(cell => {
                        let width = cell.getBoundingClientRect().width;
                        if (key === "organization") {
                            width = Math.min(width, ORGANIZATION_MAX_EM * parseFloat(getComputedStyle(cell).fontSize));
                        }
                        playerWidths[key] = Math.max(playerWidths[key], width);
                    });
                });
            });
            stageTables.forEach(table => {
                table.querySelectorAll(".rr-player-rows").forEach(grid => {
                    grid.style.gridTemplateColumns = playerWidths.name + "px " + playerWidths.organization + "px";
                });
            });
            stageTables.forEach(table => {
                Array.from(table.rows).forEach(row => Array.from(row.cells).forEach(cell => {
                    const type = kind(cell);
                    let width = cell.getBoundingClientRect().width / cell.colSpan;
                    if (type === "name" && !table.querySelector(".rr-player-rows")) {
                        width = Math.min(width, NAME_MAX_EM * parseFloat(getComputedStyle(cell).fontSize));
                    }
                    widths[type] = Math.max(widths[type], width);
                }));
            });
            stageTables.forEach(table => {
                const header = Array.from(table.rows[0].cells);
                // Collapsed outside borders add half a border on each side.
                const border = parseFloat(getComputedStyle(header[0]).borderLeftWidth) || 0;
                table.style.tableLayout = "fixed";
                table.style.width = (header.reduce((sum, cell) => sum + widths[kind(cell)] * cell.colSpan, 0) + border) + "px";
                Array.from(table.rows).forEach(row => Array.from(row.cells).forEach(cell => {
                    const width = widths[kind(cell)] * cell.colSpan + "px";
                    Object.assign(cell.style, {width, minWidth: width, maxWidth: width});
                    if (kind(cell) === "name" && !cell.querySelector(".rr-player-rows")) {
                        cell.style.whiteSpace = "normal";
                        cell.querySelectorAll(".rr-header-inner").forEach(line => {
                            line.style.whiteSpace = "normal";
                            line.querySelectorAll(".rr-name-word").forEach(word => {
                                word.style.whiteSpace = "normal";
                                word.style.maxWidth = "100%";
                            });
                        });
                    }
                }));
            });
            stageTables.forEach(table => {
                table.querySelectorAll(".rr-player-organization").forEach(fitOrganization);
                const rows = Array.from(table.rows).slice(1);
                const height = Math.max(0, ...rows.map(row => row.getBoundingClientRect().height));
                rows.forEach(row => { row.style.height = height + "px"; });
            });
        });
    };
    function align() { window.isconyAlignRoundRobinTables(document); }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", align);
    else align();
    if (document.fonts) document.fonts.ready.then(align);
})();
