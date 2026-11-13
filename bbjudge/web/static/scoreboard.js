(function () {
  'use strict';

  var examId = document.body.dataset.examId || '';
  var boardId = document.body.dataset.boardId || 'all';
  var rankingRows = document.getElementById('ranking-rows');
  var tickerTrack = document.getElementById('ticker-track');
  var updated = document.getElementById('scoreboard-updated');
  var lastFeedSignature = '';
  var busy = false;

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function formatNumber(value) {
    var n = Number(value);
    if (!isFinite(n)) return '—';
    return Math.abs(n - Math.round(n)) < 1e-9 ? String(Math.round(n)) : n.toFixed(1);
  }

  function statusBadge(item) {
    if (item.status === 'done') {
      var verdict = item.verdict || 'AC';
      return '<span class="badge ' + escapeHtml(verdict) + '">' + escapeHtml(verdict) + '</span>';
    }
    if (item.status === 'error') return '<span class="badge SE">系统错误</span>';
    if (item.status === 'judging') return '<span class="badge judging">评测中</span>';
    return '<span class="badge queued">排队中</span>';
  }

  function tickerItem(item) {
    var score = item.status === 'done'
      ? '<span class="muted">' + formatNumber(item.score) + '/' + formatNumber(item.max_score) + '</span>'
      : '';
    return '<span class="ticker-item">' +
      '<b>' + escapeHtml(item.student) + '</b>' +
      '<code>' + escapeHtml(item.problem_id) + '</code>' +
      statusBadge(item) + score +
      '</span>';
  }

  function renderTicker(feed) {
    var signature = JSON.stringify(feed.map(function (item) {
      return [item.id, item.status, item.verdict, item.score];
    }));
    if (signature === lastFeedSignature) return;
    lastFeedSignature = signature;

    var items = feed.length
      ? feed.map(tickerItem)
      : ['<span class="ticker-item muted">等待第一份成绩…</span>'];
    tickerTrack.innerHTML = items.join('') + items.join('');
    tickerTrack.style.animation = 'none';
    void tickerTrack.offsetWidth;
    tickerTrack.style.animation = '';
  }

  function totalCell(row) {
    var html = formatNumber(row.total);
    if (
      row.paper_max &&
      row.raw_total != null &&
      Math.abs(Number(row.raw_total) - Number(row.total)) > 0.005
    ) {
      html += '<div class="muted small">原始 ' +
        formatNumber(row.raw_total) + ' / ' + formatNumber(row.paper_max) + '</div>';
    }
    return html;
  }

  function renderRanking(ranking) {
    if (!ranking.length) {
      rankingRows.innerHTML = '<tr><td colspan="5" class="empty">还没有成绩，来交第一份吧。</td></tr>';
      return;
    }
    rankingRows.innerHTML = ranking.map(function (row) {
      return '<tr>' +
        '<td>' + escapeHtml(row.rank) + '</td>' +
        '<td><strong>' + escapeHtml(row.student) + '</strong></td>' +
        '<td>' + totalCell(row) + '</td>' +
        '<td>' + escapeHtml(row.ac) + '</td>' +
        '<td>' + escapeHtml(row.submissions) + '</td>' +
        '</tr>';
    }).join('');
  }

  async function refresh() {
    if (busy || !examId) return;
    busy = true;
    try {
      var response = await fetch('/api/scoreboard?exam_id=' + encodeURIComponent(examId) +
        '&board=' + encodeURIComponent(boardId), {
        cache: 'no-store'
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      var data = await response.json();
      if (data.enabled === false) {
        var panel = document.querySelector('.scoreboard');
        if (panel) panel.hidden = true;
        return;
      }
      renderRanking(data.ranking || []);
      renderTicker(data.feed || []);
      updated.textContent = '最后更新 ' + (data.updated_at || '').slice(11);
    } catch (error) {
      updated.textContent = '刷新失败，将自动重试…';
    } finally {
      busy = false;
    }
  }

  refresh();
  setInterval(refresh, 1200);
})();
