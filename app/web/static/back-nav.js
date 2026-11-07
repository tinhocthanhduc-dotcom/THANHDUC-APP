// Nút quay lại (←, ‹, "Về …") về đúng trang vừa đến, giữ bộ lọc/tab/trang của danh sách.
// Nhớ các trang đã mở trong tab này (sessionStorage). Bấm nút quay lại: tìm trang gần nhất trước đó
// khác đường dẫn trang hiện tại và không phải trang form vừa gửi (form tạo mới, trang lỗi POST).
// Không có trang nào phù hợp (mở link trực tiếp, tab mới) thì đi theo href cố định như cũ.
(function () {
  var KEY = 'td-nav', POST_KEY = 'td-nav-post', MAX = 40;
  function here() { return location.pathname + location.search; }
  function pathOf(url) { return url.split('?')[0]; }
  function load() { try { var s = JSON.parse(sessionStorage.getItem(KEY) || '[]'); return Array.isArray(s) ? s : []; } catch (e) { return []; } }
  function save(stack) { try { sessionStorage.setItem(KEY, JSON.stringify(stack.slice(-MAX))); } catch (e) {} }

  var cur = here();
  function visit() {
    var stack = load();
    if (pathOf(cur) === '/dang-nhap' || pathOf(cur) === '/dang-xuat') return;
    var posted = null;
    try { posted = JSON.parse(sessionStorage.getItem(POST_KEY) || 'null'); sessionStorage.removeItem(POST_KEY); } catch (e) {}
    var idx = -1;
    for (var i = stack.length - 1; i >= 0; i--) if (stack[i].u === cur) { idx = i; break; }
    if (idx >= 0) {
      // Đã từng mở trang này (quay lại, tải lại, lưu xong về chính trang): cắt phần phía sau.
      stack = stack.slice(0, idx + 1);
      stack[idx].t = false;
    } else {
      stack.push({ u: cur, t: false });
    }
    // Trang trả về từ POST tại địa chỉ action khác trang form (thường là báo lỗi): không quay lại đây được.
    if (posted && posted.action === cur && posted.from !== cur) stack[stack.length - 1].t = true;
    save(stack);
  }
  visit();
  // Trình duyệt lấy lại trang từ bộ nhớ (nút back của máy): ghi nhận lại là đang ở trang này.
  window.addEventListener('pageshow', function (e) { if (e.persisted) visit(); });

  // Rời trang bằng form POST: nếu là form tạo mới/sửa (…/moi, …/sua) thì quay lại sẽ bỏ qua nó,
  // để từ chứng từ vừa tạo bấm ← không mở lại form trống. Trang chứng từ thì vẫn giữ.
  var FORM_PAGE = /\/(moi|sua)$/;
  window.addEventListener('pagehide', function () {
    var form = document.querySelector('form[data-submitting]');
    if (!form) return;
    var s = load();
    if (FORM_PAGE.test(pathOf(cur)) && s.length && s[s.length - 1].u === cur) { s[s.length - 1].t = true; save(s); }
    try {
      var a = document.createElement('a'); a.href = form.getAttribute('action') || location.href;
      sessionStorage.setItem(POST_KEY, JSON.stringify({ action: a.pathname + a.search, from: cur }));
    } catch (e) {}
  });

  function target() {
    var s = load(), mine = pathOf(cur);
    var end = s.length;
    if (end && s[end - 1].u === cur) end -= 1;
    for (var i = end - 1; i >= 0; i--) {
      if (s[i].t || pathOf(s[i].u) === mine) continue;
      return s[i].u;
    }
    return null;
  }
  window.tdBackTarget = target;

  var SELECTOR = 'a.ph-back, a.tho-back, .in-tools a.back, a[aria-label^="Quay lại"], a[aria-label^="Về "]';
  function isBack(a) {
    if (a.hasAttribute('data-no-back') || a.target === '_blank' || a.hasAttribute('download')) return false;
    if (a.matches(SELECTOR)) return true;
    // Link chữ "← Danh sách", "← Đơn mua"… ở đầu trang.
    return /^←/.test((a.textContent || '').trim());
  }
  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a || !isBack(a)) return;
    var href = a.getAttribute('href') || '';
    if (/^(#|javascript:|mailto:|tel:)/i.test(href) || a.origin !== location.origin) return;
    var to = target();
    if (!to || to === a.pathname + a.search) return;
    e.preventDefault();
    location.href = to;
  });
})();
