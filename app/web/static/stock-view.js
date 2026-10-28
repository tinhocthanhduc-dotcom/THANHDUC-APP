// Xem phiếu kho: lọc dòng (không dấu) và chia 25 dòng một trang; xác nhận trước khi hủy phiếu đã ghi.
(function() {
  var box = document.querySelector('[data-sk-view-lines]');
  document.querySelectorAll('[data-confirm-void]').forEach(function(el) {
    el.addEventListener('submit', function(e) { if (!window.confirm('Hủy phiếu đã ghi và đảo các dòng sổ kho?')) e.preventDefault(); });
  });
  if (!box || !box.querySelector('[data-sk-pager]')) return;
  var SIZE = 25, page = 0;
  var rows = [].slice.call(box.querySelectorAll('[data-sk-vrow]'));
  var q = box.querySelector('[data-sk-vq]'), info = box.querySelector('[data-sk-pinfo]');
  var prev = box.querySelector('[data-sk-prev]'), next = box.querySelector('[data-sk-next]');
  function plain(t) { return String(t || '').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D').toLowerCase(); }
  rows.forEach(function(r) { r._t = plain(r.getAttribute('data-text')); });
  function show() {
    var words = plain(q ? q.value : '').split(/\s+/).filter(Boolean);
    var hit = rows.filter(function(r) { return words.every(function(w) { return r._t.indexOf(w) >= 0; }); });
    var pages = Math.max(1, Math.ceil(hit.length / SIZE));
    if (page >= pages) page = pages - 1;
    rows.forEach(function(r) { r.hidden = true; });
    hit.slice(page * SIZE, page * SIZE + SIZE).forEach(function(r) { r.hidden = false; });
    info.textContent = hit.length ? 'Dòng ' + (page * SIZE + 1) + '–' + Math.min(hit.length, page * SIZE + SIZE) + ' / ' + hit.length + (hit.length < rows.length ? ' (lọc từ ' + rows.length + ')' : '') : 'Không có dòng khớp';
    prev.disabled = page === 0; next.disabled = page >= pages - 1;
  }
  prev.addEventListener('click', function() { page--; show(); });
  next.addEventListener('click', function() { page++; show(); });
  if (q) q.addEventListener('input', function() { page = 0; show(); });
  show();
})();
