// Hộp xem nhanh phiếu: bấm số phiếu ở danh sách, Tổng quan, trang khách... mở hộp chỉ đọc, không chuyển trang.
// Ctrl/Cmd/Shift+bấm, bấm giữ (menu trình duyệt) hoặc nút "Mở đầy đủ" vẫn mở trang như cũ.
// Máy chủ trả mẩu HTML ở /xem-nhanh?u=<đường dẫn>; lỗi (quyền, không tìm thấy, hết phiên) thì đi tới trang như cũ.
(function () {
  if (!window.fetch || typeof HTMLDialogElement === 'undefined') return;
  // Giữ khớp với PATTERNS trong app/web/quick_view.py.
  var PATTERNS = [
    /^\/(dich-vu|xem-dich-vu|quay-sua)\/\d+$/, /^\/don-ban\/\d+$/, /^\/bao-gia\/\d+$/, /^\/don-mua\/\d+$/,
    /^\/phieu-kho\/\d+$/, /^\/bao-thu\/\d+$/, /^\/thu-chi\/\d+$/, /^\/tra-hang\/\d+$/,
    /^\/san-xuat\/[A-Z]{2,4}-\d{2}-\d+$/, /^\/tho-tra-hang\/\d+$/
  ];
  // Màn của thợ giữ như cũ: thợ cần thao tác trên phiếu.
  if (/^\/(tho|giao-don|hang-toi-giu)(\/|$)/.test(location.pathname)) return;

  var dialog = null, stack = [], pushed = false, seq = 0;

  function quickPath(a) {
    if (!a || a.origin !== location.origin || a.target === '_blank' || a.hasAttribute('download')) return null;
    if (a.closest('[data-no-quick]') || a.matches('.btn, .primary, [data-next-primary], .ph-back, .tho-back')) return null;
    var path = a.pathname;
    if (path === location.pathname) return null;
    if (/^\/bao-thu\//.test(path) && /^\/bao-thu(\/|$)/.test(location.pathname)) return null; // Hàng đợi Báo thu chọn phiếu tại chỗ.
    if (/return_to=tho/.test(a.search)) return null;
    for (var i = 0; i < PATTERNS.length; i++) if (PATTERNS[i].test(path)) return path;
    return null;
  }

  function ensure() {
    if (dialog) return dialog;
    dialog = document.createElement('dialog');
    dialog.className = 'qv';
    dialog.setAttribute('aria-label', 'Xem nhanh phiếu');
    document.body.appendChild(dialog);
    dialog.addEventListener('click', function (e) {
      if (e.target === dialog) { close(); return; } // Bấm ra ngoài (vùng nền).
      if (e.target.closest('[data-qv-close]')) { close(); return; }
      if (e.target.closest('[data-qv-back]')) { stack.pop(); load(stack[stack.length - 1]); return; }
      var full = e.target.closest('[data-qv-full]');
      if (full && !e.metaKey && !e.ctrlKey && !e.shiftKey) {
        // Thay mục lịch sử của hộp bằng trang đầy đủ: back từ trang đó về thẳng danh sách.
        e.preventDefault(); location.replace(full.href); return;
      }
      var rel = e.target.closest('a[data-qv-related]');
      if (rel && !e.metaKey && !e.ctrlKey && !e.shiftKey) {
        var path = quickPath(rel) || (PATTERNS.some(function (p) { return p.test(rel.pathname); }) ? rel.pathname : null);
        if (path) { e.preventDefault(); stack.push({ path: path, href: rel.href }); load(stack[stack.length - 1]); }
      }
    });
    dialog.addEventListener('cancel', function (e) { e.preventDefault(); close(); }); // Esc
    return dialog;
  }

  function load(item) {
    var box = ensure(), mine = ++seq;
    box.innerHTML = '<p class="qv-loading">Đang mở ' + item.path.split('/').pop() + '…</p>';
    if (!box.open) box.showModal();
    fetch('/xem-nhanh?u=' + encodeURIComponent(item.path), { credentials: 'same-origin', headers: { 'X-Quick-View': '1' } })
      .then(function (res) {
        if (!res.ok || res.redirected) throw new Error('fallback');
        return res.text();
      })
      .then(function (html) {
        if (mine !== seq) return;
        box.innerHTML = html;
        var back = box.querySelector('[data-qv-back]');
        if (back) back.hidden = stack.length < 2;
        var focus = box.querySelector('[data-qv-full]') || box.querySelector('[data-qv-close]');
        if (focus) focus.focus({ preventScroll: true });
      })
      .catch(function () { if (mine === seq) location.href = item.href; });
  }

  function open(path, href) {
    stack = [{ path: path, href: href }];
    if (!pushed) { try { history.pushState({ qv: 1 }, '', location.href); pushed = true; } catch (e) {} }
    load(stack[0]);
  }

  function shut() {
    seq++;
    stack = [];
    if (dialog && dialog.open) dialog.close();
    if (dialog) dialog.innerHTML = '';
  }

  function close() {
    if (pushed && history.state && history.state.qv) { history.back(); return; } // popstate đóng hộp.
    pushed = false; shut();
  }

  // Nút back của điện thoại/trình duyệt: đóng hộp thay vì rời trang.
  window.addEventListener('popstate', function () { if (pushed) { pushed = false; shut(); } });
  window.addEventListener('pageshow', function (e) { if (e.persisted) { pushed = false; shut(); } });

  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a || (dialog && dialog.contains(a))) return;
    var path = quickPath(a);
    if (!path) return;
    e.preventDefault();
    open(path, a.href);
  });
  window.tdQuickView = { open: function (href) { var a = document.createElement('a'); a.href = href; var p = quickPath(a); if (p) open(p, a.href); return !!p; } };
})();
