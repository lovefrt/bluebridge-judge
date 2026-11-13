(function () {
  'use strict';

  var createForm = document.getElementById('create-problem-form');
  var typeSelect = document.getElementById('problem-type');
  var answersRow = document.getElementById('answers-row');
  var comparatorRow = document.getElementById('comparator-row');
  var createMessage = document.getElementById('create-message');
  var testForm = document.getElementById('add-test-form');
  var testMessage = document.getElementById('test-message');

  if (window.ProblemEditor) {
    window.ProblemEditor.init({
      editorId: 'problem-description',
      sampleListId: 'sample-list',
      addBlankSample: true
    });
  }

  function showMessage(element, text, ok) {
    if (!element) return;
    element.textContent = text;
    element.className = 'form-message ' + (ok ? 'ok' : 'err');
  }

  function toggleProblemFields() {
    if (!typeSelect) return;
    var isFill = typeSelect.value === 'fill_result';
    if (answersRow) answersRow.hidden = !isFill;
    if (comparatorRow) comparatorRow.hidden = isFill;
  }

  if (typeSelect) {
    typeSelect.addEventListener('change', toggleProblemFields);
    toggleProblemFields();
  }

  async function readError(response, fallback) {
    try {
      var data = await response.json();
      return data.error || fallback;
    } catch (error) {
      return fallback;
    }
  }

  if (createForm) {
    createForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      var form = new FormData(createForm);
      var payload = {
        id: String(form.get('id') || '').trim(),
        title: String(form.get('title') || '').trim(),
        type: String(form.get('type') || 'program'),
        score: Number(form.get('score') || 100),
        time_limit_ms: Number(form.get('time_limit_ms') || 1000),
        memory_limit_mb: Number(form.get('memory_limit_mb') || 256),
        comparator: String(form.get('comparator') || 'token'),
        description: document.getElementById('problem-description').innerHTML,
        input_description: String(form.get('input_description') || ''),
        output_description: String(form.get('output_description') || ''),
        samples: window.ProblemEditor
          ? window.ProblemEditor.collectSamples('sample-list')
          : [],
        answers: String(form.get('answers') || '')
      };

      showMessage(createMessage, '创建中…', true);
      try {
        var response = await fetch('/api/problems', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (!response.ok) {
          showMessage(createMessage, await readError(response, '创建失败'), false);
          return;
        }
        showMessage(createMessage, '创建成功，正在刷新题目列表…', true);
        window.setTimeout(function () {
          window.location.href = '/teacher';
        }, 500);
      } catch (error) {
        showMessage(createMessage, '网络错误：' + error.message, false);
      }
    });
  }

  if (testForm) {
    testForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      var form = new FormData(testForm);
      var problemId = String(form.get('problem') || '');
      if (!problemId) {
        showMessage(testMessage, '请先选择题目', false);
        return;
      }

      showMessage(testMessage, '保存中…', true);
      try {
        var response = await fetch('/api/problems/' + encodeURIComponent(problemId) + '/tests', {
          method: 'POST',
          body: form
        });
        if (!response.ok) {
          showMessage(testMessage, await readError(response, '保存失败'), false);
          return;
        }
        showMessage(testMessage, '测试点已添加，正在刷新…', true);
        window.setTimeout(function () {
          window.location.href = '/teacher?problem=' + encodeURIComponent(problemId) +
            '&_=' + Date.now() + '#add-test-panel';
        }, 500);
      } catch (error) {
        showMessage(testMessage, '网络错误：' + error.message, false);
      }
    });
  }

  document.querySelectorAll('[data-add-test]').forEach(function (button) {
    button.addEventListener('click', function () {
      var pid = button.getAttribute('data-add-test');
      var select = document.getElementById('test-problem');
      if (!select) return;
      select.value = pid;
      var panel = document.getElementById('add-test-panel');
      if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
      var nameInput = document.getElementById('test-name');
      if (nameInput) nameInput.focus({ preventScroll: true });
    });
  });

  document.querySelectorAll('[data-delete-problem]').forEach(function (button) {
    button.addEventListener('click', async function () {
      var pid = button.getAttribute('data-delete-problem');
      if (!window.confirm('确定删除题目 ' + pid + ' 吗？题目目录和测试数据都会被删除。')) return;
      var url = '/api/problems/' + encodeURIComponent(pid);
      try {
        var response = await fetch(url, { method: 'DELETE' });
        if (response.status === 409) {
          var blocked = await response.json().catch(function () { return {}; });
          var proceed = window.confirm(
            (blocked.error || '题目正在被试卷使用') +
            '\n\n是否强制删除？题目会同时从相关试卷中移除，空试卷会被删除。'
          );
          if (!proceed) return;
          response = await fetch(url + '?force=1', { method: 'DELETE' });
        }
        if (!response.ok) {
          window.alert(await readError(response, '删除失败'));
          return;
        }
        window.location.reload();
      } catch (error) {
        window.alert('网络错误：' + error.message);
      }
    });
  });
})();
