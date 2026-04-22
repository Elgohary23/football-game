document.addEventListener('DOMContentLoaded', function() {
    console.log('Football Manager loaded');
    
    const buttons = document.querySelectorAll('.btn');
    buttons.forEach(btn => {
        btn.addEventListener('click', function(e) {
            if (this.href && this.href.includes('advance')) {
                this.textContent = 'Loading...';
            }
        });
    });

    const fitnessBars = document.querySelectorAll('.fitness-fill');
    fitnessBars.forEach(bar => {
        const width = parseInt(bar.style.width);
        if (width < 30) {
            bar.style.background = '#f44336';
        } else if (width < 60) {
            bar.style.background = '#ff9800';
        }
    });
});