document.addEventListener('DOMContentLoaded', function() {
    var navToggle = document.getElementById('navToggle');
    var navMenu = document.getElementById('navMenu');
    if (navToggle && navMenu) {
        navToggle.addEventListener('click', function() {
            navMenu.classList.toggle('active');
        });
    }

    document.addEventListener('click', function(e) {
        document.querySelectorAll('.dropdown-menu').forEach(function(menu) {
            var parent = menu.closest('.dropdown');
            if (parent && !parent.contains(e.target)) {
                menu.style.display = '';
            }
        });
    });

    document.querySelectorAll('.flash-message').forEach(function(msg, index) {
        setTimeout(function() {
            msg.style.transition = 'opacity 0.5s';
            msg.style.opacity = '0';
            setTimeout(function() { msg.remove(); }, 500);
        }, 4000 + (index * 200));
    });

    document.querySelectorAll('.progress-fill').forEach(function(bar) {
        var width = parseInt(bar.style.width);
        if (width < 30) {
            bar.style.background = '#e74c3c';
        } else if (width < 60) {
            bar.style.background = '#f39c12';
        }
    });

    // Simulation progress overlay - use fetch instead of native submit
    var simForms = document.querySelectorAll('form');
    for (var i = 0; i < simForms.length; i++) {
        var form = simForms[i];
        if (form.action && (form.action.indexOf('/advance_') > -1 || form.action.indexOf('/continue') > -1 || form.action.indexOf('/simulate_to_date') > -1)) {
            form.addEventListener('submit', function(e) {
                e.preventDefault();
                var overlay = document.getElementById('simOverlay');
                if (overlay) {
                    overlay.classList.add('active');
                    var fill = document.getElementById('simProgressFill');
                    var text = document.getElementById('simProgressText');
                    if (fill && text) {
                        var progress = 0;
                        var interval = setInterval(function() {
                            progress += Math.random() * 8 + 2;
                            if (progress > 95) progress = 95;
                            fill.style.width = progress + '%';
                            text.textContent = Math.round(progress) + '%';
                        }, 200);
                    }
                }
                var formData = new FormData(this);
                fetch(this.action, { method: 'POST', body: formData })
                    .then(function(r) { window.location.href = r.url; })
                    .catch(function() { window.location.href = '/dashboard'; });
            });
        }
    }

    // New game via fetch to avoid navigation timing issues
    var newGameForms = document.querySelectorAll('.new-game-form');
    for (var j = 0; j < newGameForms.length; j++) {
        newGameForms[j].addEventListener('submit', function(e) {
            e.preventDefault();
            var btn = this.querySelector('.btn');
            if (btn) { btn.textContent = 'جاري التحميل...'; }
            var formData = new FormData(this);
            fetch(this.action, { method: 'POST', body: formData })
                .then(function(r) { window.location.href = r.url; })
                .catch(function() { window.location.href = '/load'; });
        });
    }
});

// Unread messages live badge (poll every 30s)
document.addEventListener('DOMContentLoaded', function() {
    var unreadBadge = document.getElementById('unreadBadge');
    if (unreadBadge) {
        function refreshUnread() {
            fetch('/inbox_unread', { headers: { 'Accept': 'application/json' } })
                .then(function(r) {
                    if (!r.ok) throw new Error('unread ' + r.status);
                    return r.json();
                })
                .then(function(data) {
                    var n = Number(data.unread || 0);
                    unreadBadge.textContent = n;
                    unreadBadge.classList.toggle('badge-hidden', n === 0);
                })
                .catch(function() {});
        }
        refreshUnread();
        setInterval(refreshUnread, 30000);
    }
});

// Live match driver (live.html)
document.addEventListener('DOMContentLoaded', function() {
    var initial = document.getElementById('liveInitial');
    if (!initial) return;

    var liveId = parseInt(initial.dataset && initial.dataset.liveId);
    var matchId = liveId;
    var payload = {};

    try { payload = JSON.parse(initial.textContent); } catch (e) { payload = {}; }
    if (!matchId) {
        var pathParts = window.location.pathname.split('/');
        for (var k = pathParts.length - 1; k >= 0; k--) {
            if (pathParts[k] === 'live' && pathParts[k + 1]) {
                matchId = parseInt(pathParts[k + 1]);
                break;
            }
        }
    }

    var stateUrl = '/live/' + matchId + '/state';
    var advanceUrl = '/live/' + matchId + '/advance';
    var subUrl = '/live/' + matchId + '/sub';
    var finishUrl = '/live/' + matchId + '/finish';

    var homeBench = document.getElementById('liveHomeBench');
    var awayBench = document.getElementById('liveAwayBench');
    var feed = document.getElementById('liveFeed');
    var clock = document.getElementById('liveClock');
    var score = document.getElementById('liveScore');

    function csrfToken() {
        var input = document.getElementById('csrfToken');
        return input ? input.value : '';
    }

    function renderState(data) {
        if (!data) data = payload;
        if (data && data.player_team_key) playerTeamKey = data.player_team_key;
        if (score) score.textContent = (data.home_score || 0) + ' - ' + (data.away_score || 0);
        if (clock) clock.textContent = data.finished ? 'انتهت المباراة' : 'الدقيقة ' + (data.minute || 0);
        if (document.getElementById('liveHomePoss')) document.getElementById('liveHomePoss').textContent = (data.home_possession || 0) + '%';
        if (document.getElementById('liveAwayPoss')) document.getElementById('liveAwayPoss').textContent = (data.away_possession || 0) + '%';
        if (document.getElementById('hPossBar')) document.getElementById('hPossBar').style.width = (data.home_possession || 0) + '%';
        if (document.getElementById('aPossBar')) document.getElementById('aPossBar').style.width = (100 - (data.home_possession || 0)) + '%';
        if (document.getElementById('liveHomeXg')) document.getElementById('liveHomeXg').textContent = data.home_xg;
        if (document.getElementById('liveAwayXg')) document.getElementById('liveAwayXg').textContent = data.away_xg;
        if (document.getElementById('liveHomeShots')) document.getElementById('liveHomeShots').textContent = (data.home_shots || 0) + ' (' + (data.home_sot || 0) + ')';
        if (document.getElementById('liveAwayShots')) document.getElementById('liveAwayShots').textContent = (data.away_shots || 0) + ' (' + (data.away_sot || 0) + ')';
        if (document.getElementById('liveHomeCards')) document.getElementById('liveHomeCards').textContent = (data.home_yellows || 0) + ' ' + (data.home_reds || 0);
        if (document.getElementById('liveAwayCards')) document.getElementById('liveAwayCards').textContent = (data.away_yellows || 0) + ' ' + (data.away_reds || 0);

        if (feed && data.events) {
            var visible = Math.max(0, data.events.length - 60);
            var html = '';
            for (var idx = visible; idx < data.events.length; idx++) {
                var ev = data.events[idx];
                var label = teamLabel(ev.team);
                var minute = ev.minute || 0;
                var txt = minute + "' " + label;
                var desc = describeEvent(ev);
                html += '<div class="live-event">' + txt + ' ' + desc + '</div>';
            }
            feed.innerHTML = html;
            feed.scrollTop = feed.scrollHeight;
        }

        renderLineups(data);
    }

    // --- live lineups + substitutions ---------------------------------
    var playerTeamKey = payload.player_team_key || 'home_xp';
    var selectedIn = null;
    var selectedOut = null;
    var subMsg = document.getElementById('liveSubMsg');

    function playerMeta(data, id) {
        return (data.players && data.players[id]) || { name: '?' + id, position: '' };
    }

    function renderList(el, ids, data, opts) {
        if (!el) return;
        el.innerHTML = '';
        (ids || []).forEach(function(id) {
            var meta = playerMeta(data, id);
            var chip = document.createElement('span');
            chip.className = 'live-chip ' + (meta.position || '').toLowerCase();
            chip.dataset.id = id;
            if (opts.selectable) {
                chip.classList.add('clickable');
                chip.dataset.kind = opts.kind;
            }
            chip.innerHTML = '<span class="lc-name">' + escapeHtml(meta.name || '?') + '</span>' +
                '<span class="lc-pos">' + escapeHtml(meta.position || '') + '</span>' +
                (meta.rating != null ? '<span class="lc-rating">' + meta.rating + '</span>' : '');
            el.appendChild(chip);
        });
        if (opts.selectable) {
            el.querySelectorAll('.live-chip.clickable').forEach(function(chip) {
                chip.addEventListener('click', function() {
                    selectForSub(chip, opts.kind);
                });
            });
        }
    }

    function selectForSub(chip, kind) {
        var id = chip.dataset.id;
        var teamKey = playerTeamKey;
        // Toggle only the clicked side: clearing the other selection here
        // made it impossible to ever hold both picks, so no sub could
        // complete (pitch click cleared bench pick and vice versa).
        if (kind === 'pitch') {
            selectedOut = (selectedOut === id) ? null : id;
        } else {
            selectedIn = (selectedIn === id) ? null : id;
        }
        refreshSubHighlight();
        if (selectedOut && selectedIn) {
            sendSub(teamKey, selectedOut, selectedIn);
        } else {
            var hint = '';
            if (kind === 'pitch' && selectedOut) hint = 'الآن اختر البديل من الاحتياط';
            if (kind === 'bench' && selectedIn) hint = 'الآن اختر من سيخرج من التشكيلة';
            if (subMsg) subMsg.textContent = hint;
        }
    }

    function refreshSubHighlight() {
        var cols = [document.getElementById('liveHomeCol'), document.getElementById('liveAwayCol')];
        for (var c = 0; c < cols.length; c++) {
            if (!cols[c]) continue;
            cols[c].querySelectorAll('.live-chip').forEach(function(ch) {
                var id = ch.dataset.id;
                ch.classList.remove('sub-in', 'sub-out');
                if (playerTeamKey === 'home_xp' && c === 0) {
                    if (selectedIn === id) ch.classList.add('sub-in');
                    if (selectedOut === id) ch.classList.add('sub-out');
                }
                if (playerTeamKey === 'away_xp' && c === 1) {
                    if (selectedIn === id) ch.classList.add('sub-in');
                    if (selectedOut === id) ch.classList.add('sub-out');
                }
            });
        }
    }

    function sendSub(teamKey, outId, inId) {
        postTo(subUrl, { team_key: teamKey, player_out: outId, player_in: inId }, function(res) {
            if (subMsg) {
                subMsg.textContent = res && res.message ? res.message : (res.ok ? 'تم التبديل!' : 'تعذر التبديل');
            }
            selectedIn = null;
            selectedOut = null;
            refreshSubHighlight();
        });
    }

    function renderLineups(data) {
        var ownIsHome = playerTeamKey === 'home_xp';
        var homePitch = document.getElementById('liveHomePitch');
        var homeBench = document.getElementById('liveHomeBench');
        var awayPitch = document.getElementById('liveAwayPitch');
        var awayBench = document.getElementById('liveAwayBench');
        var playing = data.minute >= 1 && !data.finished;

        renderList(homePitch, data.home_onpitch, data, {
            selectable: playing && ownIsHome, kind: 'pitch'
        });
        renderList(homeBench, data.home_bench, data, {
            selectable: playing && ownIsHome, kind: 'bench'
        });
        renderList(awayPitch, data.away_onpitch, data, {
            selectable: playing && !ownIsHome, kind: 'pitch'
        });
        renderList(awayBench, data.away_bench, data, {
            selectable: playing && !ownIsHome, kind: 'bench'
        });
        refreshSubHighlight();
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function(ch) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
        });
    }

    function teamLabel(key) {
        if (key === 'home') return document.getElementById('liveHomeName') ? document.getElementById('liveHomeName').textContent : 'المنزل';
        if (key === 'away') return document.getElementById('liveAwayName') ? document.getElementById('liveAwayName').textContent : 'الضيف';
        return '⚽';
    }

    function describeEvent(ev) {
        var t = ev.type || '';
        var player = ev.player || 'لاعب';
        var map = {
            'goal': 'هدف! ' + player,
            'shot': 'تسديدة من ' + player,
            'corner': 'ركنية',
            'foul': 'خطأ من ' + player,
            'freekick': 'ضربة حرة بواسطة ' + player,
            'penalty': 'ضربة جزاء - ' + (ev.result === 'goal' ? 'هدف!' : 'أهدرها') + ' ' + player,
            'yellow_card': 'بطاقة صفراء لـ ' + player,
            'red_card': 'بطاقة حمراء لـ ' + player,
            'injury': 'إصابة ' + player,
            'sub': 'تبديل: ' + (ev.player_in || '') + ' بدلاً من ' + (ev.player_out || ''),
            'half_time': 'نهاية الشوط الأول',
            'full_time': 'نهاية المباراة'
        };
        return map[t] || t;
    }

    function refreshState() {
        fetch(stateUrl, { headers: { 'Accept': 'application/json' } })
            .then(function(r) {
                if (!r.ok) throw new Error('state ' + r.status);
                return r.json();
            })
            .then(renderState)
            .catch(function() {});
    }

    function postTo(url, data, then) {
        var body = new URLSearchParams();
        var keys = Object.keys(data || {});
        for (var i = 0; i < keys.length; i++) {
            body.append(keys[i], data[keys[i]]);
        }
        fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': csrfToken() },
            body: body.toString()
        })
            .then(function(r) {
                if (!r.ok) throw new Error('post ' + r.status);
                return r.json();
            })
            .then(function(res) {
                if (res && res.finished) {
                    window.location.href = '/match_report/' + matchId;
                } else if (then) {
                    then(res);
                }
                refreshState();
            })
            .catch(refreshState);
    }

    document.getElementById('btnMinute1').addEventListener('click', function() {
        postTo(advanceUrl, { minutes: 1 }, refreshState);
    });
    document.getElementById('btnMinute5').addEventListener('click', function() {
        postTo(advanceUrl, { minutes: 5 }, refreshState);
    });
    document.getElementById('btnFinish').addEventListener('click', function() {
        postTo(finishUrl, {});
    });

    renderState(payload);
    refreshState();
    setInterval(refreshState, 5000);
});