/* Tactics center: mentality, full tactical payload and set-piece routines.
 *
 * Collects every tactical control on the page (team instructions, set-piece
 * takers, pitch line-up roles + individual instructions) and saves it in one
 * POST to /tactics/save. The backend validates roles/takers and caps PIs.
 */
document.addEventListener('DOMContentLoaded', function() {
    const data = window.TACTICS_DATA || {};
    const csrfTokenEl = document.getElementById('csrfToken');
    const csrfToken = csrfTokenEl ? csrfTokenEl.value : '';

    let mentality = parseInt(data.mentality, 10);
    if (!mentality || mentality < 1 || mentality > 5) mentality = 3;

    const balance = document.getElementById('tacticsBalance');
    const balanceLabel = document.getElementById('tacticsBalanceLabel');
    const tacticsMsg = document.getElementById('tacticsMsg');
    const setPiecesMsg = document.getElementById('setPiecesMsg');

    function val(id) {
        const el = document.getElementById(id);
        return el ? el.value : '';
    }

    function refreshBalance() {
        if (balance) balance.style.width = (mentality * 20) + '%';
        const labels = {1: 'دفاعي جداً', 2: 'دفاعي', 3: 'متوازن', 4: 'هجومي', 5: 'هجومي جداً'};
        if (balanceLabel) balanceLabel.textContent = labels[mentality] || 'متوازن';
    }

    document.querySelectorAll('.mentality-btn').forEach(function(btn) {
        btn.addEventListener('click', function() {
            document.querySelectorAll('.mentality-btn').forEach(function(other) {
                other.classList.remove('active');
            });
            btn.classList.add('active');
            mentality = parseInt(btn.dataset.m, 10) || 3;
            refreshBalance();
        });
    });
    refreshBalance();

    function showMessage(text, isError) {
        [tacticsMsg, setPiecesMsg].forEach(function(el) {
            if (!el) return;
            el.textContent = text;
            el.style.color = isError ? '#e74c3c' : '#2ecc71';
        });
    }

    function collectPayload() {
        const payload = {
            mentality: mentality,
            in_possession: {
                passing_directness: val('tp_directness'),
                tempo: val('tp_tempo'),
                width: val('tp_width'),
                crosses: val('tp_crossing'),
                be_more_expressive: val('tp_creativity') === 'expressive',
            },
            out_of_possession: {
                pressing_intensity: parseInt(val('tp_pressing'), 10) || 2,
                defensive_line: val('tp_dline'),
                engagement: val('tp_loe'),
                counterpress: val('tp_counter_press') === 'on',
            },
            transitions: {
                counter_attack: val('tp_counter') === 'counter',
                gk_distribution: val('tp_gk_dist'),
            },
            set_pieces: {
                pen: val('penaltyTakerSelect'),
                fk: val('directFKSelect'),
                corner_l: val('cornerLeftSelect'),
                corner_r: val('cornerRightSelect'),
                long_throw: val('longThrowSelect'),
            },
        };

        const layout = val('formationLayout');
        if (layout) payload.formation = layout;

        if (window.TacticsPitch && typeof window.TacticsPitch.getSlots === 'function') {
            payload.slots = window.TacticsPitch.getSlots();
        }
        return payload;
    }

    async function saveCompleteTactics() {
        showMessage('جارٍ الحفظ...', false);
        try {
            const response = await fetch('/tactics/save', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken,
                },
                body: JSON.stringify(collectPayload()),
            });
            const result = await response.json();
            if (!result.success) {
                showMessage(result.message || 'فشل حفظ الخطة.', true);
                return result;
            }
            let text = 'تم حفظ الخطة الكاملة!';
            if (result.familiarity != null) {
                text += ' الألفة التكتيكية: ' + result.familiarity + '%';
            }
            showMessage(text, false);
            return result;
        } catch (error) {
            showMessage('خطأ في الاتصال بالخادم.', true);
            return {success: false};
        }
    }

    const saveBtn = document.getElementById('saveFullTactics');
    if (saveBtn) saveBtn.addEventListener('click', function(e) {
        e.preventDefault();
        saveCompleteTactics();
    });

    // Progressive enhancement: without JS the form still posts to the
    // legacy /formation/takers endpoint; with JS the full payload is saved.
    const setPiecesForm = document.getElementById('setPiecesForm');
    if (setPiecesForm) setPiecesForm.addEventListener('submit', function(e) {
        e.preventDefault();
        saveCompleteTactics();
    });

    window.saveCompleteTactics = saveCompleteTactics;
});
