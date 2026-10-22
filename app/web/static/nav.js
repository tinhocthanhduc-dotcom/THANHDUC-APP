'use strict';
// Chỉ điều chỉnh cách hiện menu trên trình duyệt; quyền nằm hoàn toàn ở máy chủ.
const webMenu = document.querySelector('[data-web-menu]');
const wideMenu = window.matchMedia('(min-width: 901px)');
const store = {
  get(key) { try { return localStorage.getItem(key); } catch (e) { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch (e) {} },
};
if (webMenu) {
  const resizeMenu = () => { webMenu.open = wideMenu.matches; };
  resizeMenu();
  wideMenu.addEventListener('change', resizeMenu);

  let searching = false, before = null;
  // Nhóm đang mở được nhớ trên máy này; nhóm chứa trang hiện tại luôn mở.
  const groups = Array.from(webMenu.querySelectorAll('[data-nav-group]'));
  let opened = [];
  try { opened = JSON.parse(store.get('td-nav-open') || '[]'); } catch (e) { opened = []; }
  groups.forEach((group) => {
    if (opened.includes(group.dataset.navGroup)) group.open = true;
    group.addEventListener('toggle', () => {
      if (searching) return;
      const now = groups.filter((g) => g.open).map((g) => g.dataset.navGroup);
      store.set('td-nav-open', JSON.stringify(now));
    });
  });

  // Thu gọn thành cột biểu tượng (máy tính); bấm nhóm khi đang thu gọn thì mở rộng lại.
  const root = document.documentElement;
  if (store.get('td-nav-mini') === '1') root.classList.add('nav-mini');
  const mini = document.querySelector('[data-nav-mini]');
  if (mini) mini.addEventListener('click', () => {
    root.classList.toggle('nav-mini');
    store.set('td-nav-mini', root.classList.contains('nav-mini') ? '1' : '0');
  });
  groups.forEach((group) => group.querySelector('summary').addEventListener('click', (event) => {
    if (!root.classList.contains('nav-mini') || !wideMenu.matches) return;
    event.preventDefault();
    root.classList.remove('nav-mini');
    store.set('td-nav-mini', '0');
    group.open = true;
  }));

  // Tìm trang: gõ không dấu cũng được; nhóm có trang khớp tự mở.
  const fold = (text) => text.normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D').toLowerCase();
  const search = webMenu.querySelector('[data-nav-search]');
  const empty = webMenu.querySelector('[data-nav-empty]');
  if (search) search.addEventListener('input', () => {
    const q = fold(search.value.trim());
    if (q && !searching) before = groups.map((g) => g.open);
    searching = !!q;
    let hits = 0;
    webMenu.querySelectorAll('[data-nav-item]').forEach((link) => {
      const group = link.closest('[data-nav-group]');
      const ok = !q || fold(link.textContent).includes(q) || (group && fold(group.dataset.navGroup).includes(q));
      link.hidden = !ok;
      if (ok) hits++;
    });
    groups.forEach((group, i) => {
      const any = group.querySelector('[data-nav-item]:not([hidden])');
      group.hidden = !any;
      group.open = q ? !!any : (before ? before[i] : group.open);
    });
    webMenu.querySelectorAll('.nav-head').forEach((head) => { head.hidden = !!q; });
    if (empty) empty.hidden = !q || hits > 0;
    if (!q) before = null;
  });
  if (search) search.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    const first = webMenu.querySelector('[data-nav-item]:not([hidden])');
    if (first && search.value.trim()) { event.preventDefault(); location.href = first.href; }
  });
}
