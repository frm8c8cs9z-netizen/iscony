/* Intrinsic column widths per Stage, body row heights per table. */
(function () {
    "use strict";
    const NAME_MAX_EM = 16;

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
                });
            });
            stageTables.forEach(table => {
                Object.keys(playerWidths).forEach(key => {
                    table.querySelectorAll(".rr-player-" + key).forEach(cell => {
                        playerWidths[key] = Math.max(playerWidths[key], cell.getBoundingClientRect().width);
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
