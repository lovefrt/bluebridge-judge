(function () {
  'use strict';

  var remaining = Number(document.body.dataset.remaining || '');
  var countdown = document.getElementById('countdown');
  var submitButton = document.getElementById('submit-button');
  var form = document.getElementById('submit-form');
  var finished = false;

  function format(seconds) {
    var total = Math.max(0, Math.floor(seconds));
    var minutes = Math.floor(total / 60);
    var secs = total % 60;
    if (minutes >= 60) {
      var hours = Math.floor(minutes / 60);
      minutes = minutes % 60;
      return hours + ' 小时 ' + minutes + ' 分';
    }
    return minutes + ' 分 ' + (secs < 10 ? '0' + secs : secs) + ' 秒';
  }

  function finish() {
    if (finished) return;
    finished = true;
    if (countdown) countdown.textContent = '考试已结束';
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = '考试已结束，不能提交';
    }
    if (form) {
      form.querySelectorAll('input, select, textarea, button').forEach(function (element) {
        element.disabled = true;
      });
    }
    window.setTimeout(function () { window.location.reload(); }, 1500);
  }

  if (document.body.dataset.remaining && !isNaN(remaining) && remaining > 0) {
    var left = remaining;
    if (countdown) countdown.textContent = '剩余 ' + format(left);
    var timer = window.setInterval(function () {
      left -= 1;
      if (left <= 0) {
        window.clearInterval(timer);
        finish();
        return;
      }
      if (countdown) countdown.textContent = '剩余 ' + format(left);
    }, 1000);
  }
})();
