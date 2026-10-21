document.addEventListener('DOMContentLoaded', function() {
    const pitchEl = document.getElementById('pitch');
    const players = JSON.parse(pitchEl.dataset.players || '[]');
    const formationData = JSON.parse(pitchEl.dataset.formationData || '{}');
    const PRESET_FORMATIONS = JSON.parse(pitchEl.dataset.formations || '[]');
    const LAYOUT_KEY = '__layout__';   // reserved formation_data key (not a player)
    const DROP_RADIUS = 15;            // pitch-% snap distance to a slot
    const csrfToken = document.getElementById('csrfToken').value;
    const tacticsData = window.TACTICS_DATA || { slots: [] };

    // Per-player individual instructions, restored from the saved tactics
    // slots payload. Keyed by player id string for quick lookups.
    const playerInstructions = {};
    (function restorePlayerInstructions() {
        const raw = tacticsData.slots;
        const list = Array.isArray(raw) ? raw : Object.values(raw || {});
        list.forEach(function(slot) {
            if (!slot || slot.player_id == null) return;
            const pis = Array.isArray(slot.pi) ? slot.pi.slice(0, 3) : [];
            playerInstructions[String(slot.player_id)] = pis;
        });
    })();

    const TRAIT_META = {
        long_shots: { icon: '🎯', label: 'تسديد من بعيد' },
        tries_through_balls: { icon: '📐', label: 'تمريرات بينية' },
        set_piece_specialist: { icon: '⚽', label: 'متخصص كرات ثابتة' },
        leadership: { icon: '⭐', label: 'قيادة' },
        big_game_player: { icon: '🏆', label: 'لاعب المباريات الكبيرة' },
        hard_worker: { icon: '🔥', label: 'عمل جاد' },
        team_player: { icon: '🤝', label: 'لاعب فريق' },
        gets_forward: { icon: '➡️', label: 'يتقدم للأمام' },
        injury_prone: { icon: '🩹', label: 'معرض للإصابة' },
        temperamental: { icon: '🌡️', label: 'مزاجي' },
    };

    const pitchGrid = document.getElementById('pitch-grid');
    const bench = document.getElementById('bench');
    const playerPool = document.getElementById('playerPool');
    const countSpan = document.getElementById('formationCount');
    const layoutSelect = document.getElementById('formationLayout');

    let slots = [];           // empty slot defs of the chosen preset formation
    let activeLayoutKey = ''; // persisted into formation_data on save
    let positions = {};       // "playerId" -> {x, y}
    let placedIds = new Set();

    // Restore saved layout key + placements
    if (formationData && typeof formationData === 'object') {
        activeLayoutKey = formationData[LAYOUT_KEY] || '';
        Object.keys(formationData).forEach(pid => {
            if (pid === LAYOUT_KEY) return;
            const pos = formationData[pid];
            const pl = players.find(p => p.id === parseInt(pid));
            if (pl) { positions[pid] = pos; placedIds.add(parseInt(pid)); }
        });
    }
    if (activeLayoutKey) {
        const saved = presetByKey(activeLayoutKey);
        if (saved) {
            slots = saved.slots.slice();
            layoutSelect.value = activeLayoutKey;
        } else {
            activeLayoutKey = '';
        }
    }

    function presetByKey(key) {
        return PRESET_FORMATIONS.find(f => f.key === key) || null;
    }

    const DETAIL_TO_COARSE = {
        GK: 'GK', DEF: 'DEF', MID: 'MID', FWD: 'FWD',
        CB: 'DEF', LB: 'DEF', RB: 'DEF', LWB: 'DEF', RWB: 'DEF',
        CDM: 'MID', CM: 'MID', CAM: 'MID', LM: 'MID', RM: 'MID',
        LW: 'FWD', RW: 'FWD', CF: 'FWD', ST: 'FWD'
    };
    function coarseOf(pos) { return DETAIL_TO_COARSE[pos] || pos; }
    // Mirrors services.slot_compatibility: exact detail > same band > out.
    function slotCompat(player, slotPos) {
        const det = (player.detailed_position || player.position || '').toUpperCase();
        if (det === slotPos) return 1.0;
        const cp = coarseOf(det), cs = coarseOf(slotPos);
        if (cp === 'GK' || cs === 'GK') return 0.0;
        if (cp === cs) return 0.7;
        return 0.2;
    }

    function createPoolPlayer(player) {
        const div = document.createElement('div');
        div.className = 'pool-player';
        div.draggable = true;
        div.dataset.id = player.id;
        div.title = 'اضغط لتعبئة أول مركز متاح، أو اسحب إلى المركز الذي تريده';
        div.innerHTML = `<span class="pos-tag ${player.position}">${player.detailed_position || player.position}</span> ${player.name}`;
        div.addEventListener('dragstart', (e) => {
            e.dataTransfer.setData('text/plain', player.id.toString());
            div.classList.add('dragging');
        });
        div.addEventListener('dragend', () => {
            div.classList.remove('dragging');
        });
        // Click = place into the first empty slot he fits (layout mode only)
        div.addEventListener('click', () => tryPlaceFromPool(player));
        return div;
    }

    function createBenchPlayer(player) {
        const div = document.createElement('div');
        div.className = 'bench-player';
        div.draggable = true;
        div.dataset.id = player.id;
        div.title = 'اضغط لتعبئة أول مركز متاح، أو اسحب إلى المركز الذي تريده';
        div.innerHTML = `<span class="pos-tag ${player.position}">${player.detailed_position || player.position}</span> ${player.name}`;
        div.addEventListener('dragstart', (e) => {
            e.dataTransfer.setData('text/plain', player.id.toString());
            div.classList.add('dragging');
        });
        div.addEventListener('dragend', () => {
            div.classList.remove('dragging');
        });
        div.addEventListener('click', () => tryPlaceFromPool(player));
        return div;
    }

    function currentRole(playerId) {
        const entry = formationData[String(playerId)];
        if (entry && typeof entry === 'object' && entry.role) return entry.role;
        return 'standard';
    }

    function suitabilityFor(player, role) {
        const map = player.role_suitability_map || {};
        if (map[role] != null) return map[role];
        if (map.standard != null) return map.standard;
        return player.role_suitability || 0;
    }

    function suitabilityClass(value) {
        if (value >= 80) return 'suit-good';
        if (value >= 60) return 'suit-mid';
        return 'suit-bad';
    }

    function renderSuitabilityBadge(player, role) {
        const value = suitabilityFor(player, role);
        return `<span class="pb-suit ${suitabilityClass(value)}" title="ملاءمة الدور: ${value}%">${value}%</span>`;
    }

    function renderTraitBadges(player) {
        const traits = Array.isArray(player.traits) ? player.traits : [];
        const badges = [];
        if (player.personality === 'Leader' || traits.includes('leadership')) {
            badges.push('<span class="pb-trait" title="قائد الفريق">⭐</span>');
        }
        traits.forEach(function(trait) {
            const meta = TRAIT_META[trait];
            if (!meta || trait === 'leadership') return;
            badges.push(`<span class="pb-trait" title="${meta.label}">${meta.icon}</span>`);
        });
        return `<span class="pb-traits">${badges.slice(0, 4).join('')}</span>`;
    }

    function renderPIButton(player) {
        const count = (playerInstructions[String(player.id)] || []).length;
        return `<button type="button" class="pb-pi-btn${count ? ' has-pi' : ''}" data-pi-player="${player.id}" title="تعليمات فردية (${count}/3)" aria-label="تعليمات فردية">⚙</button>`;
    }

    function createPitchBox(player) {
        const box = document.createElement('div');
        box.className = `pitch-box ${player.position}`;
        box.dataset.id = player.id;
        box.draggable = true;
        const isInjured = player.injury_days > 0;
        const name = player.name.split(' ').slice(-1)[0];
        const savedRole = currentRole(player.id);
        box.className = `pitch-box ${player.position}${isInjured ? ' injured' : ''}`;
        box.innerHTML =
            `<span class="pb-injured" aria-hidden="true">${isInjured ? '🔴' : ''}</span>` +
            renderPIButton(player) +
            renderSuitabilityBadge(player, savedRole) +
            `<span class="pb-name" title="${player.name}">${isInjured ? '🔴 ' : ''}${name}</span>` +
            `<span class="pb-pos">${player.detailed_position || player.position}</span>` +
            renderTraitBadges(player) +
            `<select class="role-select" data-role-player="${player.id}">${roleOptions(savedRole)}</select>`;
        box.title = `${player.name} - ${player.attack}/${player.defense}`;

        box.addEventListener('dragstart', (e) => {
            e.dataTransfer.setData('text/plain', player.id.toString());
            box.classList.add('dragging');
        });
        box.addEventListener('dragend', () => {
            box.classList.remove('dragging');
        });

        // Double-click to remove from pitch
        box.addEventListener('dblclick', (e) => {
            e.stopPropagation();
            if (e.target.tagName === 'SELECT' || e.target.classList.contains('pb-pi-btn')) return;
            removeFromPitch(player.id);
        });

        return box;
    }

    function createSlotBox(slot) {
        const div = document.createElement('div');
        div.className = 'pitch-slot ' + coarseOf(slot.pos);
        div.innerHTML = `<span class="pb-pos">${slot.pos}</span><span class="sb-hint">فارغ</span>`;
        div.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.stopPropagation();
            div.classList.add('dragover');
        });
        div.addEventListener('dragleave', () => div.classList.remove('dragover'));
        div.addEventListener('drop', (e) => {
            e.preventDefault();
            e.stopPropagation();
            div.classList.remove('dragover');
            const playerId = parseInt(e.dataTransfer.getData('text/plain'));
            if (!isNaN(playerId)) placePlayerAt(playerId, slots.indexOf(slot));
        });
        return div;
    }

    function roleOptions(selected) {
        const ROLE_OPTIONS = [
            ['standard', 'متوازن'], ['target_man', 'هدف علوي'], ['poacher', 'محتال'],
            ['regista', 'ريجيستا'], ['ball_winner', 'كسّار كرات'], ['wing_back', 'ظهير جارف']
        ];
        let html = '';
        ROLE_OPTIONS.forEach(([val, label]) => {
            html += `<option value="${val}" ${selected === val ? 'selected' : ''}>${label}</option>`;
        });
        return html;
    }

    // Role change → POST to server (delegated so re-renders keep working).
    // The select-click stopPropagation is registered once here, not per
    // change, so re-renders don't stack duplicate listeners.
    pitchGrid.addEventListener('click', function(e) {
        if (e.target && e.target.classList && e.target.classList.contains('role-select')) {
            e.stopPropagation();
        }
    });
    pitchGrid.addEventListener('change', function(e) {
        const sel = e.target;
        if (!sel || !sel.classList || !sel.classList.contains('role-select')) return;
        const pid = sel.dataset.rolePlayer;
        // Keep local state in sync so the full-tactics payload sends the
        // freshly picked role even before the page is reloaded.
        const existing = formationData[pid];
        formationData[pid] = Object.assign(
            {}, (existing && typeof existing === 'object') ? existing : {},
            { role: sel.value }
        );
        const player = players.find(p => p.id === parseInt(pid));
        const box = sel.closest('.pitch-box');
        if (player && box) {
            const badge = box.querySelector('.pb-suit');
            if (badge) {
                const value = suitabilityFor(player, sel.value);
                badge.className = `pb-suit ${suitabilityClass(value)}`;
                badge.textContent = value + '%';
                badge.title = `ملاءمة الدور: ${value}%`;
            }
        }
        const fd = new URLSearchParams();
        fd.append('player_id', pid);
        fd.append('role', sel.value);
        fetch('/formation/role', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': csrfToken },
            body: fd.toString()
        }).then(r => r.json()).then(function(d) {
            if (d.success) { sel.style.borderColor = '#2ecc71'; }
        }).catch(function() {});
    });

    // --- Individual player instructions (PIs) modal --------------------
    const piModal = document.getElementById('playerInstructionsModal');
    const piTitle = document.getElementById('piModalTitle');
    const piContainer = document.getElementById('piOptionsContainer');
    const PI_LABELS = {
        shoot_more: 'التسديد أكثر',
        dribble_more: 'المراوغة أكثر',
        stay_wide: 'البقاء على الطرف',
        mark_tighter: 'الالتصاق بالخصم',
        get_further_forward: 'التقدم للأمام',
    };
    let currentEditingPlayerId = null;

    function updatePIOptionState() {
        if (!piContainer) return;
        const checked = piContainer.querySelectorAll('input.pi-checkbox:checked').length;
        piContainer.querySelectorAll('.pi-option').forEach(function(row) {
            const cb = row.querySelector('input.pi-checkbox');
            if (!cb) return;
            const blocked = checked >= 3 && !cb.checked;
            cb.disabled = blocked;
            row.classList.toggle('disabled', blocked);
            row.classList.toggle('checked', cb.checked);
        });
    }

    function openPIModal(playerId) {
        const player = players.find(p => p.id === playerId);
        if (!player || !piModal) return;
        currentEditingPlayerId = playerId;
        const selected = playerInstructions[String(playerId)] || [];
        if (piTitle) piTitle.textContent = 'تعليمات فردية — ' + player.name;
        if (piContainer) {
            piContainer.innerHTML = '';
            Object.keys(PI_LABELS).forEach(function(pi) {
                const row = document.createElement('label');
                row.className = 'pi-option';
                row.innerHTML = `<input class="pi-checkbox" type="checkbox" value="${pi}" ${selected.includes(pi) ? 'checked' : ''}> <span>${PI_LABELS[pi]}</span>`;
                row.querySelector('input').addEventListener('change', function() {
                    const count = piContainer.querySelectorAll('input.pi-checkbox:checked').length;
                    if (this.checked && count > 3) {
                        this.checked = false;
                        alert('يمكنك اختيار 3 تعليمات فردية كحد أقصى لكل لاعب.');
                    }
                    updatePIOptionState();
                });
                piContainer.appendChild(row);
            });
        }
        updatePIOptionState();
        piModal.classList.add('active');
    }

    function closePIModal() {
        if (piModal) piModal.classList.remove('active');
        currentEditingPlayerId = null;
    }

    function savePlayerInstructions() {
        if (currentEditingPlayerId == null || !piContainer) { closePIModal(); return; }
        const selected = Array.from(piContainer.querySelectorAll('input.pi-checkbox:checked'))
            .map(cb => cb.value).slice(0, 3);
        playerInstructions[String(currentEditingPlayerId)] = selected;
        closePIModal();
        renderAll();
    }

    if (piModal) {
        const piCloseBtn = document.getElementById('piModalClose');
        const piSaveBtn = document.getElementById('savePIBtn');
        if (piCloseBtn) piCloseBtn.addEventListener('click', closePIModal);
        if (piSaveBtn) piSaveBtn.addEventListener('click', savePlayerInstructions);
        piModal.addEventListener('click', function(e) {
            if (e.target === piModal) closePIModal();
        });
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') closePIModal();
        });
    }

    pitchGrid.addEventListener('click', function(e) {
        const btn = e.target.closest && e.target.closest('.pb-pi-btn');
        if (!btn) return;
        e.preventDefault();
        e.stopPropagation();
        openPIModal(parseInt(btn.dataset.piPlayer, 10));
    });

    function findSlotForPlayer(pid) {
        const pos = positions[pid];
        if (!pos) return -1;
        return slots.findIndex(s => s.x === pos.x && s.y === pos.y);
    }

    function slotOwner(idx) {
        const target = slots[idx];
        if (!target) return null;
        for (const pid of Object.keys(positions)) {
            if (positions[pid].x === target.x && positions[pid].y === target.y) return pid;
        }
        return null;
    }

    function placePlayerAt(playerId, slotIdx) {
        // Slot drop: empty → place; occupied → swap (or bench the occupant
        // when the newcomer comes from the pool/bench).
        const pid = playerId.toString();
        const slot = slots[slotIdx];
        if (!slot) return;
        if (findSlotForPlayer(pid) === slotIdx) return;
        const ownerPid = slotOwner(slotIdx);
        if (ownerPid) {
            const fromIdx = findSlotForPlayer(pid);
            if (fromIdx >= 0) {
                positions[ownerPid] = { x: slots[fromIdx].x, y: slots[fromIdx].y };
            } else {
                delete positions[ownerPid];
            }
        }
        positions[pid] = { x: slot.x, y: slot.y };
        renderAll();
    }

    // Click on a pool/bench player (layout mode): first empty slot he fits.
    function tryPlaceFromPool(player) {
        if (!slots.length) return;             // free mode: drag only
        if (placedIds.has(player.id)) return;  // already staged on the pitch
        const used = new Set(Object.keys(positions).map(pid => findSlotForPlayer(pid)));
        let target = -1;
        for (let i = 0; i < slots.length; i++) {
            if (used.has(i)) continue;
            if (target === -1) target = i;
            if (slotCompat(player, slots[i].pos) > 0) { target = i; break; }
        }
        if (target === -1) { alert('لا توجد مراكز شاغرة في الملعب!'); return; }
        placePlayerAt(player.id, target);
    }

    function removeFromPitch(playerId) {
        const pid = playerId.toString();
        const player = players.find(p => p.id === playerId);
        if (!player) return;
        delete positions[pid];
        renderAll();
    }

    function appendBox(pl, pos) {
        placedIds.add(pl.id);
        const box = createPitchBox(pl);
        box.style.left = (pos.x * pitchGrid.clientWidth / 100) + 'px';
        box.style.top = (pos.y * pitchGrid.clientHeight / 100) + 'px';
        pitchGrid.appendChild(box);
    }

    function renderAll() {
        // Keep the permanent pitch markings; only clear dynamic boxes/slots.
        pitchGrid.querySelectorAll('.pitch-box, .pitch-slot').forEach((el) => el.remove());
        bench.innerHTML = '';
        playerPool.innerHTML = '';
        placedIds.clear();

        const slotKeys = slots.map(s => s.x + ':' + s.y);
        if (slots.length) {
            slots.forEach((slot, idx) => {
                const ownerPid = slotOwner(idx);
                const owner = ownerPid ? players.find(p => p.id === parseInt(ownerPid)) : null;
                if (owner) {
                    appendBox(owner, slot);
                } else {
                    pitchGrid.appendChild(createSlotBox(slot));
                }
            });
            // Staged players whose coordinates fall outside this layout stay editable.
            Object.keys(positions).forEach(pid => {
                const pos = positions[pid];
                if (slotKeys.includes(pos.x + ':' + pos.y)) return;
                const pl = players.find(p => p.id === parseInt(pid));
                if (pl) appendBox(pl, pos);
            });
        } else {
            Object.keys(positions).forEach(pid => {
                const pl = players.find(p => p.id === parseInt(pid));
                if (pl) appendBox(pl, positions[pid]);
            });
        }

        // Fill bench and player pool with everyone not staged
        players.forEach(p => {
            if (!placedIds.has(p.id)) bench.appendChild(createBenchPlayer(p));
        });
        players.forEach(p => {
            if (!placedIds.has(p.id)) playerPool.appendChild(createPoolPlayer(p));
        });

        updateCount();
    }

    function updateCount() {
        const count = Object.keys(positions).length;
        countSpan.textContent = count + '/11 لاعب';
        const btn = document.getElementById('saveFormationBtn');
        if (count === 11) {
            btn.disabled = false;
            countSpan.style.color = '#2ecc71';
        } else {
            btn.disabled = true;
            countSpan.style.color = '#e94560';
        }
    }

    // Single delegated pitch drop: slot-aware when a preset layout is
    // active (snap to nearest slot; occupied → swap), free coordinates
    // otherwise (original behaviour).
    pitchGrid.addEventListener('dragover', (e) => e.preventDefault());
    pitchGrid.addEventListener('drop', function(e) {
        e.preventDefault();
        const playerId = parseInt(e.dataTransfer.getData('text/plain'));
        if (isNaN(playerId)) return;
        const player = players.find(p => p.id === playerId);
        if (!player) return;

        if (slots.length) {
            const rect = pitchGrid.getBoundingClientRect();
            const x = (e.clientX - rect.left) / rect.width * 100;
            const y = (e.clientY - rect.top) / rect.height * 100;
            let best = -1, bestD = Infinity;
            slots.forEach((s, i) => {
                const d = Math.pow(s.x - x, 2) + Math.pow(s.y - y, 2);
                if (d < bestD) { bestD = d; best = i; }
            });
            if (best >= 0 && bestD <= DROP_RADIUS * DROP_RADIUS &&
                findSlotForPlayer(playerId) !== best) {
                placePlayerAt(playerId, best);
            }
            return;
        }

        // Free mode: place or reposition anywhere.
        const rect = pitchGrid.getBoundingClientRect();
        let x = Math.max(5, Math.min(95, (e.clientX - rect.left) / rect.width * 100));
        let y = Math.max(5, Math.min(95, (e.clientY - rect.top) / rect.height * 100));
        positions[playerId.toString()] = { x: Math.round(x), y: Math.round(y) };
        renderAll();
    });

    bench.addEventListener('dragover', (e) => e.preventDefault());
    bench.addEventListener('drop', (e) => {
        e.preventDefault();
        const playerId = parseInt(e.dataTransfer.getData('text/plain'));
        if (!isNaN(playerId)) removeFromPitch(playerId);
    });

    playerPool.addEventListener('dragover', (e) => e.preventDefault());
    playerPool.addEventListener('drop', (e) => {
        e.preventDefault();
        const playerId = parseInt(e.dataTransfer.getData('text/plain'));
        if (!isNaN(playerId)) removeFromPitch(playerId);
    });

    // Layout select: spread the chosen preset's empty slots on the pitch.
    layoutSelect.addEventListener('change', function() {
        const fmt = presetByKey(layoutSelect.value);
        if (Object.keys(positions).length > 0 &&
            !confirm('اختيار خطة جديدة سيمسح التشكيلة الحالية من الملعب. هل تريد المتابعة؟')) {
            layoutSelect.value = activeLayoutKey || '';
            return;
        }
        activeLayoutKey = layoutSelect.value;
        slots = fmt ? fmt.slots.slice() : [];
        positions = {};
        renderAll();
    });

    // Fill by ability: best available players into the empty slots (staged
    // only — confirm with 'حفظ التشكيلة').
    var fillBtn = document.getElementById('fillByAbilityBtn');
    if (fillBtn) {
        fillBtn.addEventListener('click', function() {
            if (!slots.length) {
                alert('اختر خطة جاهزة أولاً ليتم تعبئة مراكزها!');
                return;
            }
            fillBtn.disabled = true;
            fetch('/formation/ability-fill', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify({
                    layout: activeLayoutKey,
                    exclude_ids: Object.keys(positions).map(Number)
                })
            })
            .then(r => r.json())
            .then(d => {
                fillBtn.disabled = false;
                if (!d.success) { alert(d.error || 'تعذر التعبئة'); return; }
                d.assignments.forEach(a => {
                    positions[a.player_id.toString()] = { x: a.x, y: a.y };
                });
                renderAll();
                if (d.unfilled && d.unfilled.length) {
                    alert('لم يتم ملء ' + d.unfilled.length +
                          ' مركز — لا يوجد لاعبون مؤهلون متاحون (تحقق من الإصابات).');
                }
            })
            .catch(() => {
                fillBtn.disabled = false;
                alert('خطأ في الاتصال بالخادم');
            });
        });
    }

    // Save formation (the active layout key rides along as "__layout__")
    document.getElementById('saveFormationBtn').addEventListener('click', function() {
        if (Object.keys(positions).length !== 11) {
            alert('يجب اختيار 11 لاعباً بالضبط في الملعب!');
            return;
        }
        const hasGK = Object.keys(positions).some(pid => {
            const pl = players.find(p => p.id === parseInt(pid));
            return pl && pl.position === 'GK';
        });
        if (!hasGK) {
            alert('يجب أن يكون هناك حارس مرمى (GK) واحد على الأقل في التشكيل الأساسي!');
            return;
        }
        const payload = Object.assign({}, positions);
        if (activeLayoutKey) payload[LAYOUT_KEY] = activeLayoutKey;
        fetch('/formation/save', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({positions: payload})
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                alert(data.message);
                window.location.reload();
            } else {
                alert(data.error || 'فشل في الحفظ');
            }
        })
        .catch(err => alert('خطأ في الاتصال بالخادم'));
    });

    // Reset: empty the pitch (chosen slots stay spread while one is active)
    document.getElementById('resetFormationBtn').addEventListener('click', function() {
        positions = {};
        renderAll();
    });

    // Auto-pick (rules engine): best rules-compliant XI, then reload.
    // With a preset layout active, the XI is spread across its slots by ability.
    var autoBtn = document.getElementById('autoPickBtn');
    if (autoBtn) {
        autoBtn.addEventListener('click', function() {
            const opts = {method: 'POST', headers: {'X-CSRFToken': csrfToken}};
            if (activeLayoutKey) {
                opts.headers['Content-Type'] = 'application/json';
                opts.body = JSON.stringify({layout: activeLayoutKey});
            }
            fetch('/formation/auto-pick', opts)
            .then(r => r.json())
            .then(data => {
                if (data.success) { location.reload(); }
                else { alert(data.message || 'تعذر الاختيار التلقائي'); }
            })
            .catch(err => alert('خطأ في الاتصال بالخادم'));
        });
    }

    // Make pitch boxes draggable for repositioning
    pitchGrid.addEventListener('dragstart', (e) => {
        if (e.target.classList && e.target.classList.contains('pitch-box')) {
            e.dataTransfer.setData('text/plain', e.target.dataset.id);
            e.target.classList.add('dragging');
        }
    });
    pitchGrid.addEventListener('dragend', (e) => {
        if (e.target.classList && e.target.classList.contains('pitch-box')) {
            e.target.classList.remove('dragging');
        }
    });

    // Expose the pitch state so the tactics-center save can build the full
    // payload (formation slots + roles + individual instructions).
    window.TacticsPitch = {
        getSlots: function() {
            return Object.keys(positions).map(function(pid) {
                const player = players.find(p => p.id === parseInt(pid));
                return {
                    player_id: parseInt(pid),
                    role: currentRole(pid),
                    position: player ? (player.detailed_position || player.position) : '',
                    pi: (playerInstructions[pid] || []).slice(0, 3),
                };
            });
        },
        getPlayerInstructions: function() { return Object.assign({}, playerInstructions); },
        openPlayerInstructions: openPIModal,
    };

    // Initial render: restore saved placements (and preset slots), populate
    // bench + pool, and set the count. Runs once when the page loads.
    renderAll();
});
