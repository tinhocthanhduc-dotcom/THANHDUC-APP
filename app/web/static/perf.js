// TD1: mỗi lần mở trang gửi một lần "trang thao tác được sau bao lâu", đo cả đường mạng tới máy này.
// Chỉ gửi số đo (thời gian, dung lượng), không gửi nội dung trang. Xem ở Quản trị > Tốc độ app.
(function () {
  var tag = document.querySelector('[data-perf-route]');
  if (!tag || !navigator.sendBeacon || !window.performance) return;
  function send() {
    var n = performance.getEntriesByType ? performance.getEntriesByType('navigation')[0] : null, t = performance.timing, d = {};
    if (n) d = { nav: n.type, ttfb: n.responseStart, ready: n.domContentLoadedEventEnd, load: n.loadEventEnd, bytes: n.transferSize || 0 };
    else if (t && t.navigationStart) d = { nav: '', ttfb: t.responseStart - t.navigationStart, ready: t.domContentLoadedEventEnd - t.navigationStart, load: t.loadEventEnd - t.navigationStart, bytes: 0 };
    else return;
    // Cộng cả CSS/JS/ảnh tải trong lúc mở trang (0 nếu trình duyệt lấy từ bộ nhớ đệm).
    try { (performance.getEntriesByType('resource') || []).forEach(function (r) { if (r.startTime <= d.load) d.bytes += r.transferSize || 0; }); } catch (e) {}
    var body = new FormData();
    body.append('csrf', tag.dataset.csrf); body.append('route', tag.dataset.perfRoute); body.append('nav', d.nav);
    body.append('ttfb', Math.round(d.ttfb)); body.append('ready', Math.round(d.ready)); body.append('load', Math.round(d.load));
    body.append('kb', (d.bytes / 1024).toFixed(1));
    try { navigator.sendBeacon('/ca-nhan/toc-do', body); } catch (e) {}
  }
  if (document.readyState === 'complete') setTimeout(send, 0);
  else window.addEventListener('load', function () { setTimeout(send, 0); });
})();
