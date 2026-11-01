// Màn Sửa ảnh (TUẤN duyệt mẫu D2/2 ngày 02/11/2026): đặt ảnh vào đúng khung web, căn giữa, xoay, phóng to, lật.
// Xem trước bằng CSS theo đúng công thức máy đăng web dùng (dang_web/anh.py · dat_khung):
//   khung W×H; ảnh xoay q×90° rồi lật ngang (f) rồi xoay r độ; tỷ lệ = min(W/rộng, H/cao) × z; tâm ảnh ở (W/2 + x·W, H/2 + y·H).
(function () {
  'use strict';
  var form = document.querySelector('[data-sa]');
  if (!form) return;
  var bai = form.dataset.bai === '1';
  var max = parseInt(form.dataset.max || '6', 10);
  var list = form.querySelector('[data-sa-list]');
  var addTile = form.querySelector('[data-sa-add-tile]');
  var editor = form.querySelector('[data-sa-editor]');
  var frame = form.querySelector('[data-sa-frame]');
  var big = form.querySelector('[data-sa-img]');
  var logo = form.querySelector('[data-sa-logo]');
  var zIn = form.querySelector('[data-sa-z]'), rIn = form.querySelector('[data-sa-r]');
  var zOut = form.querySelector('[data-sa-zv]'), rOut = form.querySelector('[data-sa-rv]');
  var title = form.querySelector('[data-sa-title]'), note = form.querySelector('[data-sa-note]');
  var filesInput = form.querySelector('[data-sa-files]');
  var RW = bai ? 16 : 1, RH = bai ? 9 : 1;
  var current = null, newCount = 0;

  function blank() { return { z: 1, r: 0, x: 0, y: 0, q: 0, f: false, tu: false }; }
  function cards() { return Array.prototype.slice.call(list.querySelectorAll('[data-sa-card]')); }
  function st(card) { return card._st || (card._st = blank()); }
  function save(card) {
    var s = st(card), out = { z: +s.z.toFixed(4), r: +s.r.toFixed(2), x: +s.x.toFixed(4), y: +s.y.toFixed(4), q: s.q, f: s.f ? 1 : 0 };
    if (s.tu) out.tu = 1;
    card.querySelector('[data-sa-bien]').value = JSON.stringify(out);
  }
  function mark(card) {  // đụng vào ảnh nào là tick Sửa ảnh đó
    var c = card.querySelector('[data-sa-chon]');
    if (c && !c.checked) { c.checked = true; count(); }
  }

  // ---- vẽ ảnh vào một khung theo thông số ----
  function place(box, img, s, bg) {
    var W = box.clientWidth, H = box.clientHeight, nw = img.naturalWidth, nh = img.naturalHeight;
    if (!W || !H || !nw || !nh) return;
    var iw = s.q % 2 ? nh : nw, ih = s.q % 2 ? nw : nh;
    var k = Math.min(W / iw, H / ih) * s.z;
    img.style.width = (nw * k) + 'px';
    img.style.height = (nh * k) + 'px';
    img.style.left = (W / 2 + s.x * W) + 'px';
    img.style.top = (H / 2 + s.y * H) + 'px';
    img.style.transform = 'translate(-50%,-50%) rotate(' + s.r + 'deg) scaleX(' + (s.f ? -1 : 1) + ') rotate(' + (s.q * 90) + 'deg)';
    if (bg) box.style.background = bg;
  }
  function drawMini(card) {
    var box = card.querySelector('[data-sa-pick]'), img = box.querySelector('img');
    var chon = card.querySelector('[data-sa-chon]');
    if (chon && !chon.checked && !card.dataset.moi) {  // không sửa: hiện đúng ảnh đang trên web
      img.removeAttribute('style'); box.style.background = ''; box.classList.remove('sa-on');
      return;
    }
    box.classList.add('sa-on');
    if (img.complete) place(box, img, st(card), card._bg);
  }
  function drawBig() {
    if (!current) return;
    var s = st(current);
    place(frame, big, s, current._bg);
    zIn.value = Math.round(s.z * 100); zOut.textContent = Math.round(s.z * 100) + '%';
    rIn.value = s.r; rOut.textContent = (s.r > 0 ? '+' : '') + s.r + '°';
    var on = form.querySelector('[data-sa-logo-on]');
    var trai = form.querySelector('input[name=logo_goc][value=trai]');
    logo.hidden = !(on && on.checked);
    logo.classList.toggle('sa-logo-trai', !!(trai && trai.checked));
    drawMini(current);
  }

  // ---- đo nền và tìm sản phẩm (ảnh phải cùng trang app; ảnh web đi qua /dang-web/anh-tren-web) ----
  function analyse(card, img) {
    try {
      var s = st(card), c = document.createElement('canvas'), n = 200;
      var nw = img.naturalWidth, nh = img.naturalHeight, iw = s.q % 2 ? nh : nw, ih = s.q % 2 ? nw : nh;
      var k = Math.min(1, n / Math.max(iw, ih));
      c.width = Math.max(1, Math.round(iw * k)); c.height = Math.max(1, Math.round(ih * k));
      var g = c.getContext('2d');
      g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height);
      g.translate(c.width / 2, c.height / 2);
      if (s.f) g.scale(-1, 1);
      g.rotate(s.q * Math.PI / 2);
      g.drawImage(img, -nw * k / 2, -nh * k / 2, nw * k, nh * k);
      var d = g.getImageData(0, 0, c.width, c.height).data, w = c.width, h = c.height;
      var e = Math.max(1, Math.round(Math.min(w, h) * 0.03)), ch = [[], [], []];
      for (var y = 0; y < h; y++) for (var x = 0; x < w; x++) {
        if (x < e || y < e || x >= w - e || y >= h - e) { var i = (y * w + x) * 4; ch[0].push(d[i]); ch[1].push(d[i + 1]); ch[2].push(d[i + 2]); }
      }
      var bg = ch.map(function (a) { a.sort(function (p, q) { return p - q; }); return a[a.length >> 1]; });
      if (Math.min(bg[0], bg[1], bg[2]) >= 235) bg = [255, 255, 255];
      var rows = new Array(h).fill(0), cols = new Array(w).fill(0);
      for (y = 0; y < h; y++) for (x = 0; x < w; x++) {
        i = (y * w + x) * 4;
        var l = 0.299 * Math.abs(d[i] - bg[0]) + 0.587 * Math.abs(d[i + 1] - bg[1]) + 0.114 * Math.abs(d[i + 2] - bg[2]);
        if (l > 32) { rows[y]++; cols[x]++; }
      }
      var need = 2, y0 = rows.findIndex(function (v) { return v >= need; }), x0 = cols.findIndex(function (v) { return v >= need; });
      var y1 = h - 1 - rows.slice().reverse().findIndex(function (v) { return v >= need; });
      var x1 = w - 1 - cols.slice().reverse().findIndex(function (v) { return v >= need; });
      card._bg = 'rgb(' + bg.join(',') + ')';
      card._box = (y0 < 0 || x0 < 0 || (x1 - x0) * (y1 - y0) < 0.01 * w * h) ? null
        : { x0: x0 / k, y0: y0 / k, x1: (x1 + 1) / k, y1: (y1 + 1) / k, iw: iw, ih: ih };
      return true;
    } catch (err) { return false; }  // ảnh không cùng trang (tải qua app lỗi): máy sẽ tự căn giữa khi xử lý
  }
  function autoCenter(card) {
    var s = st(card), img = card._img;
    if (!img || !img.naturalWidth) return;
    if (!analyse(card, img)) {
      s.tu = true; s.r = 0;
      if (card === current && note) { note.hidden = false; note.textContent = 'Không đo được ảnh này trên máy anh; máy đăng web sẽ tự căn giữa khi xử lý.'; }
      save(card); mark(card); if (card === current) drawBig(); else drawMini(card);
      return;
    }
    var b = card._box;
    s.tu = false; s.r = 0;
    if (!b) { s.z = 1; s.x = 0; s.y = 0; }
    else {
      var W = RW * 1000, H = RH * 1000, s0 = Math.min(W / b.iw, H / b.ih);
      var k = 0.9 * Math.min(W / (b.x1 - b.x0), H / (b.y1 - b.y0));
      s.z = Math.max(0.2, Math.min(5, k / s0));
      s.x = -((b.x0 + b.x1) / 2 - b.iw / 2) * k / W;
      s.y = -((b.y0 + b.y1) / 2 - b.ih / 2) * k / H;
    }
    save(card); mark(card);
    if (card === current) drawBig(); else drawMini(card);
  }

  // ---- nạp ảnh cho từng ô ----
  function load(card, src, fallback) {
    var img = new Image();
    card._img = img;
    img.onload = function () {
      analyse(card, img);
      var mini = card.querySelector('[data-sa-pick] img');
      if (mini.src !== img.src) mini.src = img.src;
      mini.onload = function () { drawMini(card); };
      drawMini(card);
      if (card === current) { big.src = img.src; }
    };
    img.onerror = function () { if (fallback && img.src !== fallback) { img.src = fallback; } };
    img.src = src;
  }
  function pick(card) {
    if (current) current.classList.remove('sa-current');
    current = card;
    card.classList.add('sa-current');
    editor.hidden = false;
    if (note) note.hidden = true;
    var i = cards().indexOf(card) + 1;
    title.textContent = 'Ảnh ' + i + ' · đang chỉnh';
    big.onload = drawBig;
    big.src = card._img ? card._img.src : card.querySelector('[data-sa-pick] img').src;
    drawBig();
  }

  // ---- ảnh mới ----
  function addFiles(files) {
    var room = max - list.querySelectorAll('[data-sa-card][data-moi]').length;
    if (files.length > room && window.tdToast) window.tdToast('Tối đa ' + max + ' ảnh mới', 'warn');
    files = files.slice(0, Math.max(0, room));
    var shrink = window.tdShrinkPhoto || function (f) { return Promise.resolve(f); };
    Promise.all(files.map(function (f) { return shrink(f).catch(function () { return f; }); })).then(function (out) {
      out.forEach(function (file) {
        newCount += 1;
        var key = 'moi-' + newCount;
        var li = document.createElement('li');
        li.className = 'sa-card'; li.dataset.saCard = ''; li.dataset.key = key; li.dataset.moi = '1'; li._file = file;
        li.innerHTML = '<input type="hidden" name="thu_tu"><input type="hidden" data-sa-bien>' +
          '<button type="button" class="sa-mini' + (bai ? ' sa-wide' : '') + '" data-sa-pick><img alt=""></button>' +
          '<div class="sa-row"><label class="chk"><input type="checkbox" checked disabled data-sa-chon> Sửa</label><span class="hint">Ảnh mới</span></div>' +
          '<div class="sa-row"><label class="chk"><input type="radio" name="dai_dien"> Đại diện</label>' +
          (bai ? '' : '<span class="sa-move"><button type="button" class="btn btn-ghost dw-sbtn" data-sa-move="-1" aria-label="Lên trước">‹</button><button type="button" class="btn btn-ghost dw-sbtn" data-sa-move="1" aria-label="Ra sau">›</button></span>') +
          '</div><button type="button" class="btn btn-danger dw-sbtn" data-sa-bo>Bỏ ảnh mới</button>';
        li.querySelector('[data-sa-pick]').setAttribute('aria-label', 'Chỉnh ảnh mới');
        list.insertBefore(li, addTile);
        save(li);
        load(li, URL.createObjectURL(file));
        var done = function () { if (li._img.naturalWidth) autoCenter(li); else li._img.addEventListener('load', function () { autoCenter(li); }, { once: true }); };
        done();
        if (!current) pick(li);
      });
      count();
    });
  }

  function count() {
    var n = cards().filter(function (c) { var x = c.querySelector('[data-sa-chon]'); return x && x.checked; }).length;
    var out = form.querySelector('[data-sa-count]');
    if (out) out.textContent = n;
    var btn = form.querySelector('[data-sa-submit]');
    if (btn && !btn.disabled) btn.textContent = n ? 'Xem trước ' + n + ' ảnh' : 'Xem trước ảnh';
  }

  // ---- sự kiện ----
  form.addEventListener('click', function (event) {
    var t = event.target;
    var p = t.closest('[data-sa-pick]');
    if (p) { pick(p.closest('[data-sa-card]')); return; }
    var mv = t.closest('[data-sa-move]');
    if (mv) {
      var li = mv.closest('[data-sa-card]');
      if (mv.dataset.saMove === '-1' && li.previousElementSibling) list.insertBefore(li, li.previousElementSibling);
      else if (mv.dataset.saMove === '1' && li.nextElementSibling && li.nextElementSibling !== addTile) list.insertBefore(li.nextElementSibling, li);
      mv.focus(); return;
    }
    var bo = t.closest('[data-sa-bo]');
    if (bo) {
      var c = bo.closest('[data-sa-card]');
      if (c === current) { current = null; editor.hidden = true; }
      c.remove(); count(); return;
    }
    var a = t.closest('[data-sa-act]');
    if (!a) return;
    var act = a.dataset.saAct;
    if (act === 'auto-all') {
      var chosen = cards().filter(function (c) { var x = c.querySelector('[data-sa-chon]'); return x && x.checked; });
      if (!chosen.length && window.tdToast) window.tdToast('Tick “Sửa” ở ảnh cần căn trước', 'warn');
      chosen.forEach(autoCenter);
      return;
    }
    if (!current) return;
    var s = st(current);
    if (act === 'q-') s.q = (s.q + 3) % 4;
    else if (act === 'q+') s.q = (s.q + 1) % 4;
    else if (act === 'f') s.f = !s.f;
    else if (act === 'reset') { current._st = blank(); s = current._st; }
    if (act === 'auto') { autoCenter(current); return; }
    if (act === 'q-' || act === 'q+' || act === 'f') { s.tu = false; if (current._img) analyse(current, current._img); }
    save(current); mark(current); drawBig();
  });
  zIn.addEventListener('input', function () { if (!current) return; var s = st(current); s.z = zIn.value / 100; s.tu = false; save(current); mark(current); drawBig(); });
  rIn.addEventListener('input', function () { if (!current) return; var s = st(current); s.r = parseFloat(rIn.value) || 0; s.tu = false; save(current); mark(current); drawBig(); });
  form.addEventListener('change', function (event) {
    var t = event.target;
    if (t.matches('[data-sa-add]')) { var f = Array.prototype.slice.call(t.files || []); t.value = ''; addFiles(f); return; }
    if (t.matches('[data-sa-all]')) {
      cards().forEach(function (c) { var x = c.querySelector('[data-sa-chon]'); if (x && !x.disabled) { x.checked = t.checked; drawMini(c); } });
      count(); return;
    }
    if (t.matches('[data-sa-chon]')) { drawMini(t.closest('[data-sa-card]')); count(); if (t.checked) pick(t.closest('[data-sa-card]')); return; }
    if (t.matches('[data-sa-logo-on], input[name=logo_goc]')) drawBig();
  });

  // kéo để dời, lăn chuột / chụm 2 ngón để phóng to
  var ptr = {}, start = null;
  frame.addEventListener('pointerdown', function (e) {
    if (!current) return;
    frame.setPointerCapture(e.pointerId);
    ptr[e.pointerId] = { x: e.clientX, y: e.clientY };
    var ids = Object.keys(ptr), s = st(current);
    start = { x: s.x, y: s.y, z: s.z, px: e.clientX, py: e.clientY, d: ids.length === 2 ? dist() : 0 };
  });
  function dist() { var v = Object.keys(ptr).map(function (k) { return ptr[k]; }); return Math.hypot(v[0].x - v[1].x, v[0].y - v[1].y); }
  frame.addEventListener('pointermove', function (e) {
    if (!current || !ptr[e.pointerId] || !start) return;
    ptr[e.pointerId] = { x: e.clientX, y: e.clientY };
    var s = st(current), ids = Object.keys(ptr);
    if (ids.length === 2) {
      if (!start.d) { start.d = dist(); start.z = s.z; }
      s.z = Math.max(0.2, Math.min(4, start.z * dist() / start.d));
    } else {
      s.x = start.x + (e.clientX - start.px) / frame.clientWidth;
      s.y = start.y + (e.clientY - start.py) / frame.clientHeight;
    }
    s.tu = false; save(current); mark(current); drawBig();
  });
  function up(e) { delete ptr[e.pointerId]; var s = current && st(current); start = s ? { x: s.x, y: s.y, z: s.z, px: e.clientX, py: e.clientY, d: 0 } : null; if (!Object.keys(ptr).length) start = null; }
  frame.addEventListener('pointerup', up); frame.addEventListener('pointercancel', up);
  frame.addEventListener('wheel', function (e) {
    if (!current) return;
    e.preventDefault();
    var s = st(current);
    s.z = Math.max(0.2, Math.min(4, s.z * (e.deltaY < 0 ? 1.05 : 1 / 1.05)));
    s.tu = false; save(current); mark(current); drawBig();
  }, { passive: false });
  frame.addEventListener('keydown', function (e) {
    if (!current) return;
    var s = st(current), step = e.shiftKey ? 0.05 : 0.01, used = true;
    if (e.key === 'ArrowLeft') s.x -= step; else if (e.key === 'ArrowRight') s.x += step;
    else if (e.key === 'ArrowUp') s.y -= step; else if (e.key === 'ArrowDown') s.y += step;
    else used = false;
    if (used) { e.preventDefault(); s.tu = false; save(current); mark(current); drawBig(); }
  });
  window.addEventListener('resize', function () { drawBig(); cards().forEach(drawMini); });

  // ---- gửi: đánh số lại ảnh mới theo thứ tự trên màn, gom tệp vào ô ảnh ----
  form.addEventListener('submit', function () {
    var dt = null;
    try { dt = new DataTransfer(); } catch (e) { dt = null; }
    var n = 0;
    cards().forEach(function (c) {
      if (!c.dataset.moi) return;
      n += 1;
      var key = 'moi-' + n;
      c.querySelector('input[name=thu_tu]').value = key;
      c.querySelector('[data-sa-bien]').name = 'bien_' + key;
      var r = c.querySelector('input[name=dai_dien]');
      if (r) r.value = key;
      if (dt) dt.items.add(c._file);
    });
    if (dt) filesInput.files = dt.files;
  });

  // ---- khởi đầu ----
  cards().forEach(function (c) { save(c); load(c, c.dataset.src, c.dataset.fallback); });
  if (cards().length) pick(cards()[0]);
  count();
})();
