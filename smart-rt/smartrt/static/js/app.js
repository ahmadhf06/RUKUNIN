(function () {
  var body = document.body;
  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-toggle-nav]') || e.target.classList.contains('overlay')) body.classList.toggle('nav-open');
    if (e.target.closest('[data-print]')) window.print();
    var d = e.target.closest('[data-demo]');
    if (d) { var p = d.dataset.demo.split(':'); document.getElementById('username').value = p[0]; document.getElementById('password').value = p[1]; }
    var g = e.target.closest('[data-geo]');
    if (g && navigator.geolocation) {
      g.textContent = 'Mencari lokasi…';
      navigator.geolocation.getCurrentPosition(function (pos) {
        document.getElementById('lat').value = pos.coords.latitude.toFixed(6);
        document.getElementById('lng').value = pos.coords.longitude.toFixed(6);
        g.textContent = 'Lokasi terisi ✓';
      }, function () { g.textContent = 'Gagal mengambil lokasi'; });
    }
  });
  document.addEventListener('submit', function (e) {
    var m = e.target.getAttribute('data-confirm');
    if (m && !confirm(m)) e.preventDefault();
  });
  // Sinkronisasi antarperangkat: tarik notifikasi & angka dashboard dari server secara berkala.
  function get(url, cb) {
    fetch(url, { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) { if (j) cb(j); }).catch(function () {});
  }
  function poll() {
    if (document.hidden) return;
    get('/api/notifikasi', function (j) {
      document.querySelectorAll('[data-notif-count]').forEach(function (el) { el.textContent = j.unread; el.hidden = !j.unread; });
    });
    if (document.querySelector('[data-stat]')) get('/api/stats', function (j) {
      document.querySelectorAll('[data-stat]').forEach(function (el) { var v = j.cards[el.dataset.stat]; if (v !== undefined) el.textContent = v; });
    });
  }
  if (document.querySelector('.sidebar')) setInterval(poll, 20000);
  setTimeout(function () { document.querySelectorAll('.flash.ok').forEach(function (f) { f.style.display = 'none'; }); }, 6000);
})();
