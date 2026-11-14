/* Stage form bracket blocks. Placement and validation come from the server. */
"use strict";
function bracketRoundTokens(text) {
    return String(text || "").replace(/[０-９]/g, c => String.fromCharCode(c.charCodeAt(0) - 0xFEE0))
        .trim().split(/[\s,、，]+/).filter(Boolean);
}
function activeBracketBlocks() {
    const blocks = [...document.querySelectorAll(".stage-bracket")];
    return document.getElementById("id_use_multi_bracket").checked ? blocks : blocks.slice(0, 1);
}
function currentBracketStartRounds() {
    return activeBracketBlocks().map(block => bracketRoundTokens(block.querySelector(".b-rounds").value).map(v => /^\d+$/.test(v) ? String(Number(v)) : v));
}
function hasClearedStartRounds(initial) {
    if (!initial.some(rounds => rounds.length)) return false;
    return activeBracketBlocks().some(block => block.initialStartRounds.length
        && !bracketRoundTokens(block.querySelector(".b-rounds").value).length);
}
function syncBracketRowsFromDom() {
    return activeBracketBlocks().map(block => ({
        name: block.querySelector(".b-name").value,
        pair_count: Number(block.querySelector(".b-count").value) || null,
        start_rounds: block.querySelector(".b-rounds").value,
    }));
}
function writeBracketRows() {
    const rows = syncBracketRowsFromDom();
    document.getElementById("id_bracket_list").value = JSON.stringify(rows);
    const stageType = document.getElementById("id_stage_type");
    const isTournament = !stageType || stageType.value === "tournament";
    if (isTournament) {
        document.getElementById("id_pair_count").value = rows.reduce((sum, row) => sum + (row.pair_count || 0), 0) || "";
        document.getElementById("id_start_rounds").value = rows[0]?.start_rounds || "";
    }
}
function refreshBracketMode() {
    const multi = document.getElementById("id_use_multi_bracket").checked;
    const blocks = [...document.querySelectorAll(".stage-bracket")];
    blocks.forEach((block, i) => {
        block.hidden = !multi && i > 0;
        block.querySelector(".bracket-head").hidden = !multi;
        block.querySelector(".b-remove").hidden = blocks.length < 2;
    });
    document.getElementById("tournament-bracket-add-row").hidden = !multi;
    writeBracketRows();
}
function setupBracketRowEditing() {
    const list = document.getElementById("tournament-bracket-blocks");
    const input = document.getElementById("id_bracket_list");
    const locked = input.disabled;
    const url = document.getElementById("tournament-bracket-list-setup").dataset.previewUrl;
    let serial = 0;
    function textNode(tag, cls, text) {
        const node = document.createElement(tag); node.className = cls; node.textContent = text; return node;
    }
    async function requestPreview(block, standard = false) {
        clearTimeout(block.previewTimer);
        const version = block.previewVersion = (block.previewVersion || 0) + 1;
        if (block.previewController) block.previewController.abort();
        const controller = block.previewController = new AbortController();
        const rounds = block.querySelector(".b-rounds");
        const count = block.querySelector(".b-count");
        const status = block.querySelector(".status");
        const box = block.querySelector(".svg-box");
        const sizeLabel = block.querySelector(".b-size");
        const params = new URLSearchParams({start_rounds: standard ? "" : rounds.value, pair_count: count.value});
        try {
            const response = await fetch(url + "?" + params, {signal: controller.signal, headers: {Accept: "application/json"}});
            if (!response.ok) throw new Error("preview request failed");
            const data = await response.json();
            if (version !== block.previewVersion || !block.isConnected) return;
            if (standard && data.valid && data.start_rounds.length) {
                rounds.value = data.start_rounds.join(","); sync(block);
            }
            status.replaceChildren();
            const explicit = bracketRoundTokens(rounds.value).length > 0;
            status.appendChild(textNode("div", "mode", explicit
                ? `開始回戦を指定(${count.value}組)。参加ペア数は数字の個数から自動で入ります。`
                : "標準の配置(自動)で作ります。"));
            if (!data.valid) {
                const err = textNode("div", "error", data.error); err.setAttribute("role", "alert"); status.appendChild(err);
                const chips = textNode("div", "chips", "");
                const values = data.start_rounds.length ? data.start_rounds : bracketRoundTokens(rounds.value);
                values.forEach((v, i) => chips.appendChild(textNode("span", "chip" + (i + 1 === data.entry_number ? " bad" : ""), `${i + 1}:${v}`)));
                status.appendChild(chips); box.classList.add("stale");
                sizeLabel.textContent = box.childElementCount ? "直前の正しい形を薄く表示しています" : "";
                return;
            }
            box.classList.remove("stale");
            if (data.size === 1) {
                status.appendChild(textNode("div", "hint", "1組だけなので試合は作られません。"));
                box.replaceChildren(); sizeLabel.textContent = "1組 / 1枠 / 0回戦まで"; return;
            }
            const counts = new Map();
            data.start_rounds.forEach(r => counts.set(r, (counts.get(r) || 0) + 1));
            const summary = textNode("div", "summary", "");
            [...counts.keys()].sort((a,b) => a-b).forEach(r => summary.appendChild(textNode("span", "pill" + (r >= 3 ? " r3" : r === 2 ? " r2" : ""), `${r}回戦から ${counts.get(r)}組`)));
            status.appendChild(summary);
            sizeLabel.textContent = `${data.start_rounds.length}組 / ${data.size}枠 / ${data.round_labels.length}回戦まで`;
            drawPreview(box, data.slots, data.size, data.start_rounds, data.round_labels);
        } catch (error) {
            if (error.name === "AbortError" || version !== block.previewVersion) return;
            status.replaceChildren(textNode("div", "error", "プレビューを取得できませんでした。入力し直すと再試行します。"));
            box.classList.add("stale");
        }
    }
    function sync(block) {
        const count = block.querySelector(".b-count"), tokens = bracketRoundTokens(block.querySelector(".b-rounds").value);
        count.readOnly = tokens.length > 0;
        if (tokens.length) count.value = tokens.length;
        writeBracketRows();
    }
    function schedule(block) {
        sync(block);
        // Invalidate pending responses immediately, before the debounced request.
        block.previewVersion = (block.previewVersion || 0) + 1;
        if (block.previewController) block.previewController.abort();
        clearTimeout(block.previewTimer);
        block.previewTimer = setTimeout(() => requestPreview(block), 250);
    }
    function addBracket(row) {
        const block = document.createElement("section"); block.className = "stage-bracket";
        block.innerHTML = `<div class="inputs">
            <div class="bracket-head"><label>トーナメント表の名前<input type="text" class="b-name"></label><button type="button" class="b-remove">削除</button></div>
            <label>参加ペア数<input type="number" class="b-count" min="1" inputmode="numeric"></label>
            <label>開始回戦(上の組から順に)<textarea class="b-rounds" spellcheck="false" placeholder="空欄なら標準の配置で組みます"></textarea></label>
            <p class="hint">カンマ・空白・改行のどれで区切っても構いません。全角の数字も使えます。例: 3組で1組目がシード → 2,1,1</p>
            <div class="buttons"><button type="button" class="b-standard">標準の配置を入れる</button><button type="button" class="b-clear">空欄に戻す(標準の配置)</button></div>
            <div class="status" aria-live="polite"></div></div>
            <div class="preview"><div class="preview-title"><span>プレビュー</span><span class="b-size"></span></div><div class="svg-box"></div></div>`;
        serial++;
        block.querySelectorAll("input, textarea").forEach(node => { node.id = `${node.className}-${serial}`; node.disabled = locked; });
        block.querySelectorAll("button").forEach(node => { node.disabled = locked; });
        block.querySelector(".b-name").value = row.name || "";
        block.querySelector(".b-count").value = row.pair_count ?? "";
        block.querySelector(".b-rounds").value = row.start_rounds || "";
        block.initialStartRounds = bracketRoundTokens(row.start_rounds);
        block.addEventListener("input", () => schedule(block));
        block.querySelector(".b-standard").addEventListener("click", () => requestPreview(block, true));
        block.querySelector(".b-clear").addEventListener("click", () => { block.querySelector(".b-rounds").value = ""; schedule(block); });
        block.querySelector(".b-remove").addEventListener("click", () => {
            if (list.children.length < 2) return;
            clearTimeout(block.previewTimer); block.previewController?.abort(); block.remove(); refreshBracketMode();
        });
        list.appendChild(block); sync(block); requestPreview(block); refreshBracketMode();
    }
    let rows = readBracketRowsFromInput();
    if (!rows.length) rows = [{name: "本戦", pair_count: document.getElementById("id_pair_count").value, start_rounds: document.getElementById("id_start_rounds").value}];
    rows.forEach(addBracket);
    const add = document.getElementById("tournament-bracket-add-row"); add.disabled = locked;
    add.addEventListener("click", () => addBracket({name: `トーナメント${list.children.length + 1}`}));
    document.getElementById("id_use_multi_bracket").addEventListener("change", () => { refreshBracketMode(); toggleStageSlotSetup(); });
    document.getElementById("id_stage_type")?.addEventListener("change", refreshBracketMode);
    list.closest("form").addEventListener("submit", writeBracketRows);
}
  const NS = "http://www.w3.org/2000/svg";
  function el(name, attrs, text) {
    const node = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function buildTree(slots, start, size, depth) {
    if (size === 1) return slots[start] === null ? null : { leaf: true, no: slots[start] };
    const half = size / 2;
    const up = buildTree(slots, start, half, depth - 1);
    const low = buildTree(slots, start + half, half, depth - 1);
    if (up && low) return { leaf: false, round: depth, up, low };
    return up || low;
  }
  function drawPreview(box, slots, size, values, labels) {
    const total = Math.log2(size);
    const root = buildTree(slots, 0, size, total);
    const pitch = 18, top = 26, left = 64, colW = 46;
    const n = values.length;
    const width = left + colW * (total + 1) + 8;
    const height = top + pitch * n + 6;
    const svg = el("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img",
      "aria-label": `${n}組・${size}枠のトーナメント表のプレビュー` });
    const ink = "var(--ink)", muted = "var(--muted)";
    for (let r = 1; r <= total; r++) {
      svg.appendChild(el("text", { x: left + colW * r, y: 14, "text-anchor": "middle", "font-size": 10, fill: muted }, labels[r - 1]));
    }
    let order = 0;
    const roundColor = v => v >= 3 ? "var(--seed3)" : v === 2 ? "var(--seed2)" : muted;
    function place(node) {
      if (node.leaf) {
        node.y = top + pitch * order + pitch / 2;
        order++;
        const v = values[node.no - 1];
        svg.appendChild(el("text", { x: 4, y: node.y + 4, "font-size": 11, fill: ink }, String(node.no)));
        svg.appendChild(el("text", { x: 30, y: node.y + 4, "font-size": 10, fill: roundColor(v) }, `[${v}]`));
        node.x = left - 8;
        return;
      }
      place(node.up); place(node.low);
      node.y = (node.up.y + node.low.y) / 2;
      const vx = left + colW * node.round;
      for (const child of [node.up, node.low]) {
        const sx = child.leaf ? child.x : left + colW * child.round;
        svg.appendChild(el("line", { x1: sx, y1: child.y, x2: vx, y2: child.y, stroke: ink, "stroke-width": 1 }));
      }
      svg.appendChild(el("line", { x1: vx, y1: node.up.y, x2: vx, y2: node.low.y, stroke: ink, "stroke-width": 1 }));
    }
    place(root);
    if (!root.leaf) {
      const fx = left + colW * root.round;
      svg.appendChild(el("line", { x1: fx, y1: root.y, x2: fx + colW * 0.6, y2: root.y, stroke: ink, "stroke-width": 1 }));
    }
    box.replaceChildren(svg);
  }

