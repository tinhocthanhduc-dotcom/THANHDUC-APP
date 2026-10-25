// F5 (báo cáo đồng bộ 25/10/2026): đổi loại dịch vụ lưu tại chỗ bằng fetch, không tải lại trang.
// Giữ nguyên tiền, nội dung, vật tư đang nhập; cập nhật version cho mọi form trên trang; lỗi thì trả ô chọn về giá trị cũ.
(function () {
  document.querySelectorAll('form[data-kind-inline]').forEach(function (form) {
    var select = form.querySelector('select[name=kind]'), status = form.querySelector('[data-kind-status]');
    if (!select) return;
    var previous = select.value, busy = false;
    function say(text, bad) { if (status) { status.textContent = text; status.classList.toggle('lt-bad', !!bad); } }
    select.addEventListener('change', function () {
      if (busy) return;
      busy = true; select.disabled = true; say('Đang lưu loại…');
      var body = new URLSearchParams(new FormData(form));
      fetch(form.action, { method: 'POST', body: body, credentials: 'same-origin', headers: { 'X-Inline': '1', 'Accept': 'application/json' } })
        .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
        .then(function (res) {
          if (!res.ok || !res.data.ok) {
            select.value = previous;
            say(res.data && res.data.error ? res.data.error : 'Không lưu được loại, hãy thử lại.', true);
            if (res.data && res.data.code === 'VERSION_CONFLICT') say((res.data.error || '') + ' Số đang nhập vẫn còn trên trang; tải lại khi đã sao chép xong.', true);
            return;
          }
          var old = form.querySelector('input[name=version]').value;
          document.querySelectorAll('input[name=version]').forEach(function (el) { if (el.value === old) el.value = res.data.version; });
          var key = form.querySelector('input[name=form_key]'); if (key && res.data.form_key) key.value = res.data.form_key;
          previous = select.value;
          say('Đã lưu loại: ' + res.data.label + '.');
          document.dispatchEvent(new CustomEvent('service-kind-saved', { detail: res.data }));
        })
        .catch(function () { select.value = previous; say('Mất kết nối, chưa lưu loại. Số đang nhập vẫn còn trên trang.', true); })
        .then(function () { busy = false; select.disabled = false; });
    });
  });
})();
