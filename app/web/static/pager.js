// Phân trang dùng chung: nhớ số dòng/trang của từng người, phím ← → chuyển trang.
(function () {
  var KEY = 'td-page-size';
  function get() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function set(v) { try { localStorage.setItem(KEY, v); } catch (e) {} }
  function go(size) {
    var url = new URL(location.href);
    Array.from(url.searchParams.keys()).forEach(function (k) { if (k === 'phan' || k.indexOf('trang') === 0) url.searchParams.delete(k); });
    if (size === '25') url.searchParams.delete('so'); else url.searchParams.set('so', size);
    location.href = url.pathname + url.search;
  }
  document.addEventListener('change', function (e) {
    var sel = e.target.closest && e.target.closest('[data-pager-size]');
    if (!sel) return;
    set(sel.value); go(sel.value);
  });
  var stored = get(), url = new URL(location.href);
  if (stored && stored !== '25' && !url.searchParams.has('so') && document.querySelector('[data-pager-size]')) {
    url.searchParams.set('so', stored); url.searchParams.delete('trang');
    location.replace(url.pathname + url.search);
  }
  document.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var el = document.activeElement;
    if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) return;
    var link = e.key === 'ArrowLeft' ? document.querySelector('[data-pager-prev]') : e.key === 'ArrowRight' ? document.querySelector('[data-pager-next]') : null;
    if (link) { e.preventDefault(); location.href = link.href; }
  });
})();
