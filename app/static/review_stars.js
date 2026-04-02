/**
 * Sterne 1–5 (links = 1, rechts = 5). hiddenInput = WTForms IntegerField mit type=hidden.
 */
window.hsReviewStarsSync = function (container, hiddenInput, value) {
    var ct = typeof container === 'string' ? document.querySelector(container) : container;
    var hi = typeof hiddenInput === 'string' ? document.querySelector(hiddenInput) : hiddenInput;
    if (!ct || !hi) return;
    var n = parseInt(value, 10);
    if (isNaN(n) || n < 1) n = 1;
    if (n > 5) n = 5;
    hi.value = String(n);
    ct.querySelectorAll('.star-rating-star').forEach(function (btn, idx) {
        var lit = (idx + 1) <= n;
        btn.classList.toggle('is-lit', lit);
        var ic = btn.querySelector('i');
        if (ic) ic.className = 'fas fa-star';
    });
    var row = ct.closest('.star-rating-wrap');
    var lb = row ? row.querySelector('.star-rating-value-label') : null;
    if (lb) lb.textContent = String(n);
};

window.hsReviewStarsInitOnce = function (container, hiddenInput) {
    var ct = typeof container === 'string' ? document.querySelector(container) : container;
    var hi = typeof hiddenInput === 'string' ? document.querySelector(hiddenInput) : hiddenInput;
    if (!ct || !hi || ct.dataset.hsStarsBound) return;
    ct.dataset.hsStarsBound = '1';
    ct.querySelectorAll('.star-rating-star').forEach(function (btn, idx) {
        btn.addEventListener('click', function (e) {
            e.preventDefault();
            window.hsReviewStarsSync(ct, hi, idx + 1);
        });
    });
};
