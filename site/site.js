/* LMX site — map, counters, tape. */
(function () {
  var DROPS = [[30.3339,-97.7076,3],[30.3512,-97.6890,2],[30.3210,-97.6720,2],[30.3660,-97.7130,2],[30.3080,-97.7280,1],[30.3405,-97.6555,1],[30.2510,-97.7490,3],[30.2380,-97.7290,2],[30.2670,-97.7430,2],[30.2240,-97.7610,2],[30.2560,-97.7180,1],[30.2790,-97.7620,1],[30.2035,-97.6690,3],[30.1880,-97.6980,2],[30.2180,-97.6470,2],[30.1720,-97.6620,1],[30.2300,-97.6890,1],[30.4390,-97.6200,2],[30.4570,-97.6790,1],[30.4180,-97.7550,1],[30.5080,-97.6790,1],[30.4880,-97.8180,1],[30.1330,-97.7860,1],[30.1010,-97.8390,1],[30.5350,-97.7530,1]];
  var HUB = [30.2900, -97.7000];

  function initMap() {
    var el = document.getElementById('austin-map');
    if (!el || !window.L) return;
    el.classList.add('lmx-map');
    var map = L.map(el, { center: HUB, zoom: 10, zoomControl: false, scrollWheelZoom: false });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap contributors', maxZoom: 18 }).addTo(map);
    DROPS.forEach(function (d) {
      L.circleMarker([d[0], d[1]], { radius: 3 + d[2] * 1.6, color: '#1F3864', weight: 1, fillColor: '#1F3864', fillOpacity: 0.75 }).addTo(map);
    });
    L.circleMarker(HUB, { radius: 7, color: '#D97706', weight: 2, fillColor: '#D97706', fillOpacity: 1 }).addTo(map).bindTooltip('LMX Hub One — Austin, TX');
  }

  function initCounters() {
    var host = document.querySelector('[data-stats]');
    if (!host || !('IntersectionObserver' in window)) return;
    var nodes = [].slice.call(host.querySelectorAll('[data-count]'));
    if (!nodes.length) return;
    nodes.forEach(function (n) { n.textContent = '0' + (n.getAttribute('data-suffix') || ''); });
    var io = new IntersectionObserver(function (entries) {
      if (!entries.some(function (e) { return e.isIntersecting; })) return;
      io.disconnect();
      var start = performance.now(), dur = 1400;
      (function tick(now) {
        var p = Math.min(1, (now - start) / dur), eased = 1 - Math.pow(1 - p, 3);
        nodes.forEach(function (n) {
          var target = parseInt(n.getAttribute('data-count'), 10);
          n.textContent = Math.round(target * eased).toLocaleString('en-US') + (n.getAttribute('data-suffix') || '');
        });
        if (p < 1) requestAnimationFrame(tick);
      })(performance.now());
    }, { threshold: 0.35 });
    io.observe(host);
  }

  function boot() { initMap(); initCounters(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
