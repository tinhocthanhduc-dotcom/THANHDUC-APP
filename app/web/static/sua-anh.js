// Màn Sửa ảnh (TUẤN duyệt mẫu D2/2 ngày 02/11/2026): đặt ảnh vào đúng khung web, căn giữa, xoay, phóng to, lật.
// Xem trước bằng CSS theo đúng công thức máy đăng web dùng (dang_web/anh.py · dat_khung):
//   khung W×H; ảnh xoay q×90° rồi lật ngang (f) rồi xoay r độ; tỷ lệ = min(W/rộng, H/cao) × z; tâm ảnh ở (W/2 + x·W, H/2 + y·H).
(function () {
  'use strict';
  var form = document.querySelector('[data-sa]');
  if (!form) return;
  var bai = form.dataset.bai === '1';
  var max = parseInt(form.dataset.max || '6', 10);
  // TUẤN duyệt mẫu D8/8 ngày 02/11/2026: 3 nhóm ảnh, mỗi nhóm một danh sách (data-vai); nhóm ③ khung ngang.
  var lists = Array.prototype.slice.call(form.querySelectorAll('[data-sa-list]'));
  var list = lists[0];
  var H2 = [];
  try { H2 = JSON.parse(form.dataset.h2 || '[]'); } catch (e) { H2 = []; }
  function listOf(vai) { return form.querySelector('[data-sa-list][data-vai="' + vai + '"]'); }
  function tileOf(ul) { return ul.querySelector('[data-sa-add-tile]'); }
  function isWide(card) { var ul = card.closest('[data-sa-list]'); return bai || !!(ul && ul.dataset.wide === '1'); }
  var editor = form.querySelector('[data-sa-editor]');
  var frame = form.querySelector('[data-sa-frame]');
  var big = form.querySelector('[data-sa-img]');
  var logo = form.querySelector('[data-sa-logo]'), logoImg = form.querySelector('[data-sa-logo-img]');
  var logoBox = form.querySelector('[data-sa-logo-box]'), lsIn = form.querySelector('[data-sa-ls]');
  var lsOut = form.querySelector('[data-sa-lsv]'), lPos = form.querySelector('[data-sa-lpos]');
  var gV = form.querySelector('[data-sa-gv]'), gH = form.querySelector('[data-sa-gh]');
  var zIn = form.querySelector('[data-sa-z]'), rIn = form.querySelector('[data-sa-r]');
  var zOut = form.querySelector('[data-sa-zv]'), rOut = form.querySelector('[data-sa-rv]');
  var title = form.querySelector('[data-sa-title]'), note = form.querySelector('[data-sa-note]');
  var filesInput = form.querySelector('[data-sa-files]');
  var current = null, newCount = 0;

  function blank() { return { z: 1, r: 0, x: 0, y: 0, q: 0, f: false, tu: false, l: null }; }
  function cards() { return Array.prototype.slice.call(form.querySelectorAll('[data-sa-list] [data-sa-card]')); }
  function setVai(card, vai) {
    var v = card.querySelector('[data-sa-vai]');
    if (v) v.value = vai;
    var len = card.querySelector('[data-sa-len]'), mv = card.querySelector('.sa-move');
    if (len) len.hidden = vai !== 'album';
    if (mv) mv.hidden = vai !== 'album';
  }
  function moveTo(card, vai, front) {
    var ul = listOf(vai);
    if (!ul) return;
    var first = ul.querySelector('[data-sa-card]');
    ul.insertBefore(card, front && first ? first : tileOf(ul));
    setVai(card, vai);
  }
  function lenDaiDien(card) {  // ảnh album lên làm ảnh đại diện, ảnh đại diện cũ xuống đầu album
    var dd = listOf('dai_dien');
    Array.prototype.slice.call(dd.querySelectorAll('[data-sa-card]')).forEach(function (c) { if (c !== card && !c.hidden) moveTo(c, 'album', true); });
    moveTo(card, 'dai_dien', false);
  }
  function st(card) { return card._st || (card._st = blank()); }
  function save(card) {
    var s = st(card), out = { z: +s.z.toFixed(4), r: +s.r.toFixed(2), x: +s.x.toFixed(4), y: +s.y.toFixed(4), q: s.q, f: s.f ? 1 : 0 };
    if (s.tu) out.tu = 1;
    if (s.l) { out.lx = +s.l.x.toFixed(4); out.ly = +s.l.y.toFixed(4); out.ls = +s.l.s.toFixed(4); }
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
    drawLogo();
    drawMini(current);
  }

  // ---- logo (TUẤN duyệt mẫu 09/11/2026): kéo để dời, kéo chấm xanh hoặc thanh "Cỡ logo" để đổi cỡ ----
  // Cùng công thức dat_khung: ô logo cạnh ls × cạnh ngắn khung, logo vừa trong ô; (lx, ly) là tâm logo theo tỷ lệ khung.
  var LS0 = 0.13, LMIN = 0.06, LMAX = 0.35, LM = 0.03;
  function logoOn() { var on = form.querySelector('[data-sa-logo-on]'); return !!(on && on.checked); }
  function logoPx(ls, W, H) {  // cỡ logo thật trên khung W×H
    var nw = logoImg.naturalWidth || 225, nh = logoImg.naturalHeight || 253, r = ls * Math.min(W, H), k = Math.min(r / nw, r / nh);
    return { w: nw * k, h: nh * k };
  }
  function corner(goc, ls, W, H) {
    var p = logoPx(ls, W, H), m = LM * Math.min(W, H);
    return { x: (goc === 'trai' ? m + p.w / 2 : W - m - p.w / 2) / W, y: (H - m - p.h / 2) / H, s: ls };
  }
  function lgOf(s, W, H) { return s.l || corner('phai', LS0, W, H); }
  function clampLogo(l, W, H) {
    var p = logoPx(l.s, W, H), hx = p.w / 2 / W, hy = p.h / 2 / H;
    l.x = Math.min(1 - hx, Math.max(hx, l.x)); l.y = Math.min(1 - hy, Math.max(hy, l.y));
    return l;
  }
  function drawLogo() {
    var on = logoOn();
    logo.hidden = !on || !current;
    if (logoBox) logoBox.hidden = !on;
    if (!on || !current) return;
    var W = frame.clientWidth, H = frame.clientHeight;
    if (!W || !H) return;
    var l = lgOf(st(current), W, H), p = logoPx(l.s, W, H);
    logo.style.width = p.w + 'px'; logo.style.height = p.h + 'px';
    logo.style.left = (l.x * W - p.w / 2) + 'px'; logo.style.top = (l.y * H - p.h / 2) + 'px';
    lsIn.value = Math.round(l.s * 100); lsOut.textContent = Math.round(l.s * 100) + '%';
    var e = 0.002, ct = corner('trai', l.s, W, H), cp = corner('phai', l.s, W, H), ten;
    if (Math.abs(l.y - cp.y) < e && Math.abs(l.x - cp.x) < e) ten = 'góc phải dưới';
    else if (Math.abs(l.y - ct.y) < e && Math.abs(l.x - ct.x) < e) ten = 'góc trái dưới';
    else ten = 'tự đặt (' + Math.round(l.x * 100) + '%, ' + Math.round(l.y * 100) + '%)';
    lPos.textContent = 'Vị trí: ' + ten;
  }
  function setLogo(card, l) { st(card).l = l; save(card); mark(card); if (card === current) drawLogo(); }
  function snapLogo(l, W, H) {  // hít vào lề 3% hoặc giữa khung, hiện vạch hồng
    var p = logoPx(l.s, W, H), m = LM * Math.min(W, H), t = 0.02 * Math.min(W, H), sx = null, sy = null;
    [m + p.w / 2, W / 2, W - m - p.w / 2].forEach(function (v) { if (Math.abs(l.x * W - v) < t) { l.x = v / W; sx = v; } });
    [m + p.h / 2, H / 2, H - m - p.h / 2].forEach(function (v) { if (Math.abs(l.y * H - v) < t) { l.y = v / H; sy = v; } });
    gV.hidden = sx === null; gH.hidden = sy === null;
    if (sx !== null) gV.style.left = sx + 'px';
    if (sy !== null) gH.style.top = sy + 'px';
  }
  var lDrag = null;
  logo.addEventListener('pointerdown', function (e) {
    if (!current) return;
    e.stopPropagation(); e.preventDefault();
    logo.setPointerCapture(e.pointerId);
    var W = frame.clientWidth, H = frame.clientHeight, l = lgOf(st(current), W, H);
    lDrag = { size: !!e.target.closest('[data-sa-logo-hd]'), px: e.clientX, py: e.clientY, x: l.x, y: l.y, s: l.s };
  });
  logo.addEventListener('pointermove', function (e) {
    if (!lDrag || !current) return;
    var W = frame.clientWidth, H = frame.clientHeight, dx = e.clientX - lDrag.px, dy = e.clientY - lDrag.py, l;
    if (lDrag.size) {  // góc trên trái đứng yên, kéo chấm xanh ra là to
      var p0 = logoPx(lDrag.s, W, H), m = Math.min(W, H);
      var ns = Math.min(LMAX, Math.max(LMIN, lDrag.s + (dx + dy) / m));
      var p1 = logoPx(ns, W, H);
      l = { s: ns, x: (lDrag.x * W - p0.w / 2 + p1.w / 2) / W, y: (lDrag.y * H - p0.h / 2 + p1.h / 2) / H };
      clampLogo(l, W, H);
    } else {
      l = clampLogo({ s: lDrag.s, x: lDrag.x + dx / W, y: lDrag.y + dy / H }, W, H);
      snapLogo(l, W, H);
    }
    setLogo(current, l);
  });
  function lUp() { lDrag = null; gV.hidden = true; gH.hidden = true; }
  logo.addEventListener('pointerup', lUp); logo.addEventListener('pointercancel', lUp);
  logo.addEventListener('keydown', function (e) {
    var k = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
    if (!k || !current) return;
    e.preventDefault(); e.stopPropagation();
    var W = frame.clientWidth, H = frame.clientHeight, l = lgOf(st(current), W, H), d = e.shiftKey ? 0.05 : 0.01;
    setLogo(current, clampLogo({ s: l.s, x: l.x + k[0] * d, y: l.y + k[1] * d }, W, H));
  });
  if (lsIn) lsIn.addEventListener('input', function () {
    if (!current) return;
    var W = frame.clientWidth, H = frame.clientHeight, l = lgOf(st(current), W, H);
    setLogo(current, clampLogo({ s: lsIn.value / 100, x: l.x, y: l.y }, W, H));
  });

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
      var W = isWide(card) ? 16000 : 1000, H = isWide(card) ? 9000 : 1000, s0 = Math.min(W / b.iw, H / b.ih);
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
    frame.classList.toggle('sa-wide', isWide(card));
    var ul = card.closest('[data-sa-list]'), ten = { dai_dien: 'Ảnh đại diện', album: 'Ảnh album', mo_ta: 'Ảnh trong mô tả', bai: 'Ảnh trong bài' };
    title.textContent = (ten[ul && ul.dataset.vai] || 'Ảnh') + ' · đang chỉnh';
    big.onload = drawBig;
    big.src = card._img ? card._img.src : card.querySelector('[data-sa-pick] img').src;
    drawBig();
  }

  // ---- ảnh mới ----
  function addFiles(files, ul) {
    ul = ul || list;
    var vai = ul.dataset.vai, wide = bai || ul.dataset.wide === '1';
    if (vai === 'dai_dien') files = files.slice(0, 1);
    var room = max - form.querySelectorAll('[data-sa-card][data-moi]').length;
    if (files.length > room && window.tdToast) window.tdToast('Tối đa ' + max + ' ảnh mới', 'warn');
    files = files.slice(0, Math.max(0, room));
    var shrink = window.tdShrinkPhoto || function (f) { return Promise.resolve(f); };
    Promise.all(files.map(function (f) { return shrink(f).catch(function () { return f; }); })).then(function (out) {
      out.forEach(function (file) {
        newCount += 1;
        var key = 'moi-' + newCount;
        var li = document.createElement('li');
        li.className = 'sa-card'; li.dataset.saCard = ''; li.dataset.key = key; li.dataset.moi = '1'; li._file = file;
        var trong = vai === 'mo_ta' || vai === 'bai', sp = !bai && !trong;
        var chon = '';
        if (trong) {
          chon = '<label class="sa-vitri"><b>Đặt dưới mục</b><select data-sa-vitri>' +
            H2.map(function (h, i) { return '<option value="' + i + '">H2 · ' + esc(h) + '</option>'; }).join('') +
            '<option value="">Cuối ' + (bai ? 'bài' : 'mô tả') + '</option></select></label>';
        }
        li.innerHTML = '<input type="hidden" name="thu_tu"><input type="hidden" data-sa-bien><input type="hidden" data-sa-vai value="' + vai + '">' +
          '<button type="button" class="sa-mini' + (wide ? ' sa-wide' : '') + '" data-sa-pick><img alt=""></button>' +
          '<div class="sa-row"><label class="chk"><input type="checkbox" checked disabled data-sa-chon> Sửa</label><span class="hint">' + (/^ai-/.test(file.name || '') ? 'Ảnh AI vẽ' : 'Ảnh mới') + '</span>' +
          (sp ? '<span class="sa-move"' + (vai === 'album' ? '' : ' hidden') + '><button type="button" class="btn btn-ghost dw-sbtn" data-sa-move="-1" aria-label="Lên trước">‹</button><button type="button" class="btn btn-ghost dw-sbtn" data-sa-move="1" aria-label="Ra sau">›</button></span>' : '') +
          '</div>' + chon +
          '<div class="sa-row">' + (sp ? '<button type="button" class="btn btn-ghost dw-sbtn" data-sa-len' + (vai === 'album' ? '' : ' hidden') + '>Lên đại diện</button>' : '') +
          '<button type="button" class="btn btn-danger dw-sbtn" data-sa-bo>Bỏ ảnh mới</button></div>';
        li.querySelector('[data-sa-pick]').setAttribute('aria-label', 'Chỉnh ảnh mới');
        if (vai === 'dai_dien') {  // chỉ 1 ảnh đại diện: sản phẩm thì ảnh cũ xuống album, bài viết thì ảnh cũ được thay
          Array.prototype.slice.call(ul.querySelectorAll('[data-sa-card]')).forEach(function (c) {
            if (c.hidden) return;
            if (bai) { c.hidden = true; li._thayCu = c; } else { moveTo(c, 'album', true); }
          });
        }
        ul.insertBefore(li, tileOf(ul));
        save(li);
        load(li, URL.createObjectURL(file));
        var done = function () { if (li._img.naturalWidth) autoCenter(li); else li._img.addEventListener('load', function () { autoCenter(li); }, { once: true }); };
        done();
        if (!current) pick(li);
      });
      count();
    });
  }

  function esc(t) { return String(t).replace(/[&<>"']/g, function (c) { return '&#' + c.charCodeAt(0) + ';'; }); }

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
      var li = mv.closest('[data-sa-card]'), ul = li.parentNode;
      if (mv.dataset.saMove === '-1' && li.previousElementSibling) ul.insertBefore(li, li.previousElementSibling);
      else if (mv.dataset.saMove === '1' && li.nextElementSibling && li.nextElementSibling !== tileOf(ul)) ul.insertBefore(li.nextElementSibling, li);
      mv.focus(); return;
    }
    var len = t.closest('[data-sa-len]');
    if (len) { var lc = len.closest('[data-sa-card]'); lenDaiDien(lc); lc.querySelector('[data-sa-pick]').focus(); return; }
    var bo = t.closest('[data-sa-bo]');
    if (bo) {
      var c = bo.closest('[data-sa-card]');
      if (c === current) { current = null; editor.hidden = true; }
      if (c._thayCu) c._thayCu.hidden = false;
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
    if (act === 'logo-trai' || act === 'logo-phai') {
      setLogo(current, corner(act === 'logo-trai' ? 'trai' : 'phai', LS0, frame.clientWidth, frame.clientHeight));
      return;
    }
    if (act === 'logo-copy') {  // chép vị trí + cỡ logo (theo tỷ lệ khung) sang các ảnh đã tick Sửa
      var l0 = lgOf(s, frame.clientWidth, frame.clientHeight), n = 0;
      cards().forEach(function (c) {
        var x = c.querySelector('[data-sa-chon]');
        if (c === current || !x || !x.checked) return;
        var w = isWide(c) ? 1600 : 1000, h = isWide(c) ? 900 : 1000;
        st(c).l = clampLogo({ x: l0.x, y: l0.y, s: l0.s }, w, h); save(c); n += 1;
      });
      if (window.tdToast) window.tdToast(n ? 'Đã chép logo sang ' + n + ' ảnh' : 'Tick “Sửa” ở ảnh cần chép logo trước', n ? 'ok' : 'warn');
      return;
    }
    if (act === 'q-') s.q = (s.q + 3) % 4;
    else if (act === 'q+') s.q = (s.q + 1) % 4;
    else if (act === 'f') s.f = !s.f;
    else if (act === 'reset') { var giu = s.l; current._st = blank(); s = current._st; s.l = giu; }  // giữ logo anh đã đặt
    if (act === 'auto') { autoCenter(current); return; }
    if (act === 'q-' || act === 'q+' || act === 'f') { s.tu = false; if (current._img) analyse(current, current._img); }
    save(current); mark(current); drawBig();
  });
  zIn.addEventListener('input', function () { if (!current) return; var s = st(current); s.z = zIn.value / 100; s.tu = false; save(current); mark(current); drawBig(); });
  rIn.addEventListener('input', function () { if (!current) return; var s = st(current); s.r = parseFloat(rIn.value) || 0; s.tu = false; save(current); mark(current); drawBig(); });
  form.addEventListener('change', function (event) {
    var t = event.target;
    if (t.matches('[data-sa-add]')) { var f = Array.prototype.slice.call(t.files || []); t.value = ''; addFiles(f, t.closest('[data-sa-list]')); return; }
    if (t.matches('[data-sa-all]')) {
      cards().forEach(function (c) { var x = c.querySelector('[data-sa-chon]'); if (x && !x.disabled) { x.checked = t.checked; drawMini(c); } });
      count(); return;
    }
    if (t.matches('[data-sa-chon]')) { drawMini(t.closest('[data-sa-card]')); count(); if (t.checked) pick(t.closest('[data-sa-card]')); return; }
    if (t.matches('[data-sa-logo-on]')) drawBig();
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
  if (logoImg) logoImg.addEventListener('load', drawLogo);

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
      c.querySelector('[data-sa-vai]').name = 'vai_' + key;
      var vt = c.querySelector('[data-sa-vitri]');
      if (vt) vt.name = 'vitri_' + key;
      if (dt) dt.items.add(c._file);
    });
    if (dt) filesInput.files = dt.files;
  });

  // Ảnh AI vẽ (ve-anh.js) bấm "Dùng ảnh này" thì thêm như ảnh mới
  form.addEventListener('sa:them-anh', function (event) { addFiles(event.detail || [], form.querySelector('[data-sa-list][data-ai]')); });

  // ---- khởi đầu ----
  cards().forEach(function (c) { save(c); load(c, c.dataset.src, c.dataset.fallback); });
  if (cards().length) pick(cards()[0]);
  count();
})();
