(function () {
  'use strict';

  function insertAtCaret(editor, node) {
    var selection = window.getSelection();
    if (selection && selection.rangeCount && editor.contains(selection.anchorNode)) {
      var range = selection.getRangeAt(0);
      range.deleteContents();
      range.insertNode(node);
      range.setStartAfter(node);
      range.collapse(true);
      selection.removeAllRanges();
      selection.addRange(range);
    } else {
      editor.appendChild(node);
    }
  }

  function imageToDataUrl(file, callback) {
    var reader = new FileReader();
    reader.onload = function () {
      var image = new Image();
      image.onload = function () {
        var maxWidth = 1200;
        if (image.width <= maxWidth) {
          callback(reader.result);
          return;
        }
        var canvas = document.createElement('canvas');
        var scale = maxWidth / image.width;
        canvas.width = maxWidth;
        canvas.height = Math.round(image.height * scale);
        canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
        callback(canvas.toDataURL('image/jpeg', 0.88));
      };
      image.onerror = function () { callback(reader.result); };
      image.src = reader.result;
    };
    reader.readAsDataURL(file);
  }

  function setupRichEditor(editorId) {
    var editor = document.getElementById(editorId);
    if (!editor) return;
    editor.addEventListener('paste', function (event) {
      var items = event.clipboardData && event.clipboardData.items;
      if (!items) return;
      for (var i = 0; i < items.length; i += 1) {
        if (items[i].type.indexOf('image/') !== 0) continue;
        var file = items[i].getAsFile();
        if (!file) continue;
        event.preventDefault();
        imageToDataUrl(file, function (dataUrl) {
          var image = document.createElement('img');
          image.src = dataUrl;
          image.alt = '题目图片';
          image.style.maxWidth = '100%';
          insertAtCaret(editor, image);
        });
        return;
      }
    });
  }

  function addSampleRow(container, data) {
    if (!container) return;
    var row = document.createElement('div');
    row.className = 'sample-row';
    row.innerHTML =
      '<div class="panel-head">' +
      '<h3>示例</h3>' +
      '<button type="button" class="link-button danger" data-remove-sample>删除</button>' +
      '</div>' +
      '<div class="form-grid">' +
      '<div class="row"><label>输入</label>' +
      '<textarea class="sample-input" rows="5" placeholder="输入数据，可留空"></textarea></div>' +
      '<div class="row"><label>输出</label>' +
      '<textarea class="sample-output" rows="5" placeholder="标准输出"></textarea></div>' +
      '</div>';
    row.querySelector('.sample-input').value = (data && data.input) || '';
    row.querySelector('.sample-output').value = (data && data.output) || '';
    container.appendChild(row);
  }

  function collectSamples(containerId) {
    var container = document.getElementById(containerId);
    if (!container) return [];
    return Array.from(container.querySelectorAll('.sample-row')).map(function (row) {
      return {
        input: row.querySelector('.sample-input').value,
        output: row.querySelector('.sample-output').value
      };
    }).filter(function (sample) {
      return sample.input.trim() || sample.output.trim();
    });
  }

  function init(options) {
    setupRichEditor(options.editorId);

    var container = document.getElementById(options.sampleListId);
    if (!container) return;
    if (options.addBlankSample && !container.querySelector('.sample-row')) {
      addSampleRow(container);
    }
    container.addEventListener('click', function (event) {
      var remove = event.target.closest('[data-remove-sample]');
      if (remove) {
        var row = remove.closest('.sample-row');
        if (row) row.remove();
      }
    });
    document.querySelectorAll('[data-add-sample]').forEach(function (button) {
      button.addEventListener('click', function () {
        addSampleRow(container);
      });
    });
  }

  window.ProblemEditor = {
    init: init,
    collectSamples: collectSamples,
    addSampleRow: addSampleRow
  };
})();
