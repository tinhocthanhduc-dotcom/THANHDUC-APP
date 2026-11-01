// Trợ lý AI cạnh chuông (chỉ giám đốc): số vàng = đề xuất chờ duyệt; mở ra duyệt hoặc từ chối nhanh ngay trang đang xem.
(function () {
  var box = document.querySelector('[data-ai-notify]');
  if (!box || !window.fetch) return;
  var badge = box.querySelector('[data-ai-count]'), list = box.querySelector('[data-ai-list]'), title = box.querySelector('[data-ai-title]');
  var EVERY = 60000, busy = false;
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text) n.textContent = text; return n; }
  function send(item, verb, button) {
    var body = new FormData(); body.append('csrf', box.dataset.csrf); body.append('form_key', item.form_key);
    body.append('next', location.pathname + location.search);
    button.disabled = true;
    fetch('/tro-ly-ai/' + item.id + '/' + verb, { method: 'POST', body: body, credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error(); location.href = '/tro-ly-ai?tab=' + (verb === 'duyet' ? 'da-duyet' : 'tu-choi') + '#ai-' + item.id; })
      .catch(function () { location.href = '/tro-ly-ai#ai-' + item.id; });
  }
  function draw(data) {
    var n = data.count || 0;
    badge.hidden = !n; badge.textContent = n > 99 ? '99+' : String(n);
    title.textContent = n ? 'Trợ lý AI · ' + n + ' việc chờ anh duyệt' : 'Trợ lý AI';
    list.textContent = '';
    if (!data.items || !data.items.length) { list.appendChild(el('p', 'mu', 'Không có đề xuất nào chờ duyệt.')); return; }
    data.items.forEach(function (item) {
      var row = el('div', 'ai-pop-item');
      var head = el('strong', '', item.title); head.insertBefore(el('span', 'ai-pop-kind', item.kind), head.firstChild);
      row.appendChild(head);
      if (item.reason) row.appendChild(el('small', '', item.reason));
      var act = el('div', 'ai-pop-actions');
      var ok = el('button', 'btn btn-ok', 'Duyệt'); ok.type = 'button';
      var no = el('button', 'btn btn-danger', 'Từ chối'); no.type = 'button';
      ok.addEventListener('click', function () { send(item, 'duyet', ok); });
      no.addEventListener('click', function () { send(item, 'tu-choi', no); });
      act.appendChild(ok); act.appendChild(no); row.appendChild(act);
      list.appendChild(row);
    });
  }
  function load() {
    if (busy || document.hidden) return; busy = true;
    fetch(box.dataset.aiNotify, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) draw(d); }).catch(function () {}).then(function () { busy = false; });
  }
  box.addEventListener('toggle', function () { if (box.open) load(); });
  document.addEventListener('visibilitychange', load);
  load(); setInterval(load, EVERY);
})();
