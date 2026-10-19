'use strict';
// Chỉ điều chỉnh cách mở menu theo kích thước; quyền nằm hoàn toàn ở máy chủ.
const webMenu = document.querySelector('[data-web-menu]');
const wideMenu = window.matchMedia('(min-width: 901px)');
if (webMenu) {
  const resizeMenu = () => { webMenu.open = wideMenu.matches; };
  resizeMenu();
  wideMenu.addEventListener('change', resizeMenu);
}
