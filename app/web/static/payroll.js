'use strict';
document.querySelectorAll('[data-print-payslip]').forEach(function(button){button.addEventListener('click',function(){window.print();});});
