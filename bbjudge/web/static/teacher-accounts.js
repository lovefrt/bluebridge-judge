(function () {
  'use strict';

  document.querySelectorAll('[data-delete-student]').forEach(function (button) {
    button.addEventListener('click', async function () {
      var studentId = button.getAttribute('data-delete-student');
      if (!window.confirm('确定删除这个学生账号吗？已有提交记录会保留，但该账号不能再登录。')) return;
      try {
        var response = await fetch('/api/students/' + encodeURIComponent(studentId), {
          method: 'DELETE'
        });
        if (!response.ok) {
          var data = await response.json().catch(function () { return {}; });
          window.alert(data.error || '删除失败');
          return;
        }
        window.location.reload();
      } catch (error) {
        window.alert('网络错误：' + error.message);
      }
    });
  });
})();
