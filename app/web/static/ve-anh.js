/* Ảnh AI vẽ cho bài viết (TUẤN duyệt mẫu D4/4, D5/5 ngày 02/11/2026): hỏi trạng thái mỗi 6 giây khi Codex đang vẽ,
   vẽ xong thì hiện ảnh. Màn Sửa ảnh: "Dùng ảnh này" thêm ảnh vào danh sách; màn xem trước: chọn 1 ảnh làm đại diện. */
(function () {
  var box = document.querySelector('[data-va]');
  if (!box) return;
  var url = box.dataset.url, cheDo = box.dataset.cheDo, body = box.querySelector('[data-va-body]');
  var nut = box.querySelector('[data-va-ve]'), note = box.querySelector('[data-va-note]');

  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return '&#' + c.charCodeAt(0) + ';'; }); }

  function ve(d) {
    if (d.trang_thai === 'loi') { body.innerHTML = '<p class="alert bad">' + esc(d.loi || 'Codex không vẽ được.') + '</p>'; }
    else if (d.trang_thai === 'xong') {
      var h = '<div class="va-list">';
      (d.anh || []).forEach(function (a, i) {
        h += '<div class="va-item"><img src="' + esc(a.url) + '" alt="Ảnh AI ' + (i + 1) + '">';
        h += cheDo === 'chon'
          ? '<label class="chk"><input type="radio" name="anh_ai" value="' + esc(a.chon) + '"' + (i ? '' : ' checked') + '> Chọn ảnh này</label>'
          : '<button type="button" class="btn btn-ghost dw-xbtn-info" data-va-dung="' + esc(a.url) + '" data-va-ten="ai-' + esc(d.id.slice(-6)) + '-' + (i + 1) + '.jpg">Dùng ảnh này</button>';
        h += '</div>';
      });
      h += '</div>';
      if (cheDo === 'chon') h += '<label class="chk"><input type="radio" name="anh_ai" value=""> Không dùng ảnh AI</label>';
      body.innerHTML = h;
    }
    if (nut && d.trang_thai !== 'cho' && d.trang_thai !== 'dang_ve' && !box.querySelector('.va-tat')) nut.disabled = false;
  }

  function hoi() {
    fetch(url, { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (!d) return setTimeout(hoi, 15000);
      if (d.trang_thai === 'cho' || d.trang_thai === 'dang_ve') return setTimeout(hoi, 6000);
      ve(d);
    }).catch(function () { setTimeout(hoi, 15000); });
  }
  if (url && box.querySelector('.va-wait')) setTimeout(hoi, 6000);

  box.addEventListener('click', function (event) {
    var b = event.target.closest('[data-va-dung]');
    if (!b) return;
    b.disabled = true;
    var form = box.closest('form');
    fetch(b.dataset.vaDung, { credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) throw new Error('tải ảnh');
      return r.blob();
    }).then(function (blob) {
      var file = new File([blob], b.dataset.vaTen, { type: blob.type || 'image/jpeg' });
      form.dispatchEvent(new CustomEvent('sa:them-anh', { detail: [file] }));
      b.textContent = '✓ Đã thêm vào danh sách';
      b.classList.add('btn-new');
      if (note) note.hidden = false;
    }).catch(function () {
      b.disabled = false;
      if (window.tdToast) window.tdToast('Chưa tải được ảnh, thử lại', 'bad');
    });
  });
})();
