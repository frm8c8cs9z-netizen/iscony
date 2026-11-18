(() => {
    const form = document.getElementById('day-addition-form');
    if (!form) return;
    const rows = document.getElementById('addition-rows');
    const total = document.getElementById('id_rows-TOTAL_FORMS');
    function update() {
        const items = rows.querySelectorAll('.addition-row');
        items.forEach((row, index) => {
            row.querySelector('.planned-number').textContent = String(BigInt(form.dataset.nextNumber) + BigInt(index));
            row.querySelectorAll('[name], [id], label[for]').forEach(el => {
                ['name', 'id', 'for'].forEach(attr => {
                    if (el.hasAttribute(attr)) el.setAttribute(attr, el.getAttribute(attr).replace(/rows-(\d+|__prefix__)-/g, `rows-${index}-`));
                });
            });
            row.querySelector('.remove-row').disabled = items.length === 1;
        });
        total.value = items.length;
        document.getElementById('add-row').disabled = items.length >= 100;
    }
    document.getElementById('add-row').addEventListener('click', () => {
        rows.insertAdjacentHTML('beforeend', document.getElementById('addition-row-template').innerHTML);
        update();
    });
    rows.addEventListener('click', event => {
        if (event.target.classList.contains('remove-row') && rows.children.length > 1) {
            event.target.closest('.addition-row').remove();
            update();
        }
    });
    update();
})();
