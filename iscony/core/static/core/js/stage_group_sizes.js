/* League-only group-size confirmation; submission uses the form's deferred requestSubmit. */
function chooseLeagueGroupChange(sizes) {
    const dialog = document.getElementById("league-group-change-dialog");
    const impact = JSON.parse(document.getElementById("league-group-change-impact").textContent);
    const removed = [], sources = [], referees = [];
    const scheduleIds = new Set();
    impact.forEach((group, index) => {
        const delta = sizes[index] - group.size;
        if (delta >= 0) return;
        group.entries.slice(delta).forEach(entry => {
            removed.push(`${group.name}: ${entry.label}`);
            entry.schedule_ids.forEach(id => scheduleIds.add(id));
        });
        group.sources.filter(source => source.rank > sizes[index]).forEach(source => sources.push(source.label));
        group.referees.filter(referee => referee.rank > sizes[index]).forEach(referee => referees.push(referee));
    });
    const remainingReferees = referees.filter(referee => !scheduleIds.has(referee.id));
    document.getElementById("league-group-change-summary").textContent = [
        "未割り付けに戻る枠:\n" + (removed.join("\n") || "なし"),
        `消える日程(コートの割り付け): ${scheduleIds.size}件`,
        `後のStageで外れる進出元: ${sources.length}件\n` + (sources.join("\n") || "なし"),
        `外れる審判の指定: ${remainingReferees.length}件\n` + (remainingReferees.map(referee => referee.label).join("\n") || "なし"),
    ].join("\n\n");
    return new Promise(resolve => {
        function finish(mode) {
            dialog.removeEventListener("click", click);
            dialog.removeEventListener("close", close);
            dialog.close();
            resolve(mode);
        }
        function click(event) {
            const mode = event.target.dataset.leagueGroupMode;
            if (mode) finish(mode === "cancel" ? null : mode);
        }
        function close() { finish(null); }
        dialog.addEventListener("click", click);
        dialog.addEventListener("close", close);
        dialog.showModal();
        dialog.querySelector('[data-league-group-mode="keep"]').focus();
    });
}
