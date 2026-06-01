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
                var self = this;
                fetch(this.action, { method: 'POST', body: formData })
                    .then(function(r) {
                        window.location.href = r.url;
                    })
                    .catch(function() {
                        window.location.href = '/dashboard';
                    });
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
                .then(function(r) {
                    window.location.href = r.url;
                })
                .catch(function() {
                    window.location.href = '/load';
                });
        });
    }
});
