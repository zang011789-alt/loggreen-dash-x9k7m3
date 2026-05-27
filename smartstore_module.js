/**
 * smartstore_module.js
 * 스마트스토어 매출 데이터 렌더링 모듈
 * 의존: window.SS_HISTORY (smartstore_history.js 로드 시 주입)
 *
 * 사용법 (meta_dashboard.html에 합칠 때):
 *   <script src="smartstore_history.js"></script>
 *   <script src="smartstore_module.js"></script>
 *   ...
 *   SS.init('ss-container');
 */

(function (global) {
  'use strict';

  // ── 상수 ─────────────────────────────────────────────────
  const BRANDS = {
    leadermune:      { label: '리더뮨(leadermune)', badge: 'LM', color: '#4ade80' },
    ridermune:       { label: '리더뮨',              badge: 'RM', color: '#22d3ee' },
    nifgen:          { label: '니프젠',              badge: 'NF', color: '#a78bfa' },
    dermacollection: { label: '더마콜렉션',          badge: 'DC', color: '#f472b6' },
  };

  const PRODUCT_KEYWORDS = {
    leadermune:      ['노즈픽션', '노즈브리즈', '노즈스파', '아토픽션', '템퍼픽션', '키즈픽션', '키로메디'],
    ridermune:       ['노즈픽션', '노즈브리즈', '노즈스파', '아토픽션', '템퍼픽션', '키즈픽션', '키로메디'],
    nifgen:          [],
    dermacollection: [],
  };

  // ── 포맷 유틸 ────────────────────────────────────────────
  function fmtNum(n) {
    if (!n && n !== 0) return '—';
    return Math.round(n).toLocaleString();
  }

  function fmtRoas(spend, revenue) {
    if (!spend || spend === 0) return '—';
    return (revenue / spend * 100).toFixed(0) + '%';
  }

  function dateRange(preset) {
    const today = new Date();
    const fmt = d => d.toISOString().slice(0, 10);
    const sub = n => { const d = new Date(today); d.setDate(d.getDate() - n); return d; };
    if (preset === 'today')     return [fmt(today),   fmt(today)];
    if (preset === 'yesterday') return [fmt(sub(1)),  fmt(sub(1))];
    if (preset === '7d')        return [fmt(sub(6)),  fmt(today)];
    if (preset === '14d')       return [fmt(sub(13)), fmt(today)];
    if (preset === '30d')       return [fmt(sub(29)), fmt(today)];
    return [fmt(sub(6)), fmt(today)]; // 기본 7일
  }

  // ── 날짜 범위 내 히스토리 집계 ───────────────────────────
  function aggregateByBrand(brandKey, startDate, endDate) {
    const h = global.SS_HISTORY || {};
    const result = {
      revenue:  0,
      orders:   0,
      products: {},  // name → { revenue, orders }
      channels: {},  // name → { revenue, orders }
      byDate:   {},  // date → { revenue, orders }
    };

    for (const [dateStr, dayData] of Object.entries(h)) {
      if (dateStr < startDate || dateStr > endDate) continue;
      const bd = dayData[brandKey];
      if (!bd) continue;

      const s = bd.summary || {};
      result.revenue += s.revenue || 0;
      result.orders  += s.orders  || 0;
      result.byDate[dateStr] = { revenue: s.revenue || 0, orders: s.orders || 0 };

      for (const p of (bd.products || [])) {
        if (!result.products[p.name]) result.products[p.name] = { revenue: 0, orders: 0 };
        result.products[p.name].revenue += p.revenue || 0;
        result.products[p.name].orders  += p.orders  || 0;
      }

      for (const c of (bd.channels || [])) {
        if (!result.channels[c.name]) result.channels[c.name] = { revenue: 0, orders: 0 };
        result.channels[c.name].revenue += c.revenue || 0;
        result.channels[c.name].orders  += c.orders  || 0;
      }
    }

    return result;
  }

  // ── KPI 카드 렌더 ─────────────────────────────────────────
  function renderKPI(agg) {
    return `
      <div class="ss-kpi-row">
        <div class="ss-kpi-card">
          <div class="ss-kpi-label">매출</div>
          <div class="ss-kpi-value">${fmtNum(agg.revenue)}</div>
        </div>
        <div class="ss-kpi-card">
          <div class="ss-kpi-label">주문 수</div>
          <div class="ss-kpi-value">${fmtNum(agg.orders)}</div>
        </div>
        <div class="ss-kpi-card">
          <div class="ss-kpi-label">객단가</div>
          <div class="ss-kpi-value">${agg.orders ? fmtNum(agg.revenue / agg.orders) : '—'}</div>
        </div>
      </div>`;
  }

  // ── 상품별 테이블 렌더 ────────────────────────────────────
  function renderProductTable(agg) {
    const rows = Object.entries(agg.products)
      .sort((a, b) => b[1].revenue - a[1].revenue)
      .map(([name, v]) => `
        <tr>
          <td>${name}</td>
          <td class="num">${fmtNum(v.revenue)}</td>
          <td class="num">${fmtNum(v.orders)}</td>
          <td class="num">${v.orders ? fmtNum(v.revenue / v.orders) : '—'}</td>
        </tr>`).join('');

    if (!rows) return '<p class="ss-empty">상품 데이터 없음</p>';

    return `
      <table class="ss-table">
        <thead>
          <tr>
            <th>상품명</th>
            <th class="num">매출</th>
            <th class="num">주문</th>
            <th class="num">객단가</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  // ── 일별 테이블 렌더 ──────────────────────────────────────
  function renderDateTable(agg) {
    const rows = Object.entries(agg.byDate)
      .sort((a, b) => b[0].localeCompare(a[0]))
      .map(([d, v]) => `
        <tr>
          <td>${d}</td>
          <td class="num">${fmtNum(v.revenue)}</td>
          <td class="num">${fmtNum(v.orders)}</td>
          <td class="num">${v.orders ? fmtNum(v.revenue / v.orders) : '—'}</td>
        </tr>`).join('');

    if (!rows) return '<p class="ss-empty">데이터 없음</p>';

    return `
      <table class="ss-table">
        <thead>
          <tr>
            <th>날짜</th>
            <th class="num">매출</th>
            <th class="num">주문</th>
            <th class="num">객단가</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  // ── 브랜드 섹션 렌더 ──────────────────────────────────────
  function renderBrandSection(brandKey, preset, viewMode) {
    const [start, end] = dateRange(preset);
    const agg  = aggregateByBrand(brandKey, start, end);
    const cfg  = BRANDS[brandKey];
    const table = viewMode === 'date' ? renderDateTable(agg) : renderProductTable(agg);

    return `
      <div class="ss-brand-section">
        <div class="ss-brand-header">
          <span class="ss-brand-badge" style="background:${cfg.color}">${cfg.badge}</span>
          <span class="ss-brand-name">${cfg.label}</span>
          <span class="ss-period">${start} ~ ${end}</span>
        </div>
        ${renderKPI(agg)}
        ${table}
      </div>`;
  }

  // ── 전체 섹션 초기화 ──────────────────────────────────────
  function init(containerId) {
    const el = document.getElementById(containerId);
    if (!el) { console.warn('SS: container not found:', containerId); return; }

    if (!global.SS_HISTORY) {
      el.innerHTML = '<p class="ss-empty">smartstore_history.js 파일 없음<br>smartstore_fetch.py 실행 후 새로고침</p>';
      return;
    }

    let preset   = '7d';
    let viewMode = 'product';  // 'product' | 'date'

    function render() {
      const sections = Object.keys(BRANDS)
        .map(k => renderBrandSection(k, preset, viewMode))
        .join('');

      el.innerHTML = `
        <div class="ss-toolbar">
          <div class="ss-presets">
            ${['today','yesterday','7d','14d','30d'].map(p =>
              `<button class="ss-btn${preset === p ? ' active' : ''}"
                       onclick="SS._setPreset('${p}')">${
                {today:'오늘',yesterday:'어제','7d':'7일','14d':'14일','30d':'30일'}[p]
              }</button>`
            ).join('')}
          </div>
          <div class="ss-views">
            <button class="ss-btn${viewMode === 'product' ? ' active' : ''}"
                    onclick="SS._setView('product')">상품별</button>
            <button class="ss-btn${viewMode === 'date' ? ' active' : ''}"
                    onclick="SS._setView('date')">일별</button>
          </div>
        </div>
        <div class="ss-sections">${sections}</div>`;
    }

    global.SS = {
      init,
      _setPreset: function(p) { preset = p;      render(); },
      _setView:   function(v) { viewMode = v;    render(); },
    };

    render();
  }

  // ── CSS 주입 ────────────────────────────────────────────
  function injectStyles() {
    if (document.getElementById('ss-styles')) return;
    const s = document.createElement('style');
    s.id = 'ss-styles';
    s.textContent = `
      .ss-toolbar {
        display: flex; gap: 12px; align-items: center;
        margin-bottom: 16px; flex-wrap: wrap;
      }
      .ss-presets, .ss-views { display: flex; gap: 6px; }
      .ss-btn {
        padding: 4px 12px; border-radius: 4px; border: 1px solid #444;
        background: #2a2a2a; color: #ccc; cursor: pointer; font-size: 13px;
      }
      .ss-btn.active { background: #3b82f6; border-color: #3b82f6; color: #fff; }
      .ss-btn:hover:not(.active) { background: #333; }
      .ss-sections { display: flex; gap: 20px; flex-wrap: wrap; }
      .ss-brand-section {
        flex: 1; min-width: 320px; background: #1e1e1e;
        border-radius: 8px; padding: 16px; border: 1px solid #333;
      }
      .ss-brand-header {
        display: flex; align-items: center; gap: 8px; margin-bottom: 14px;
      }
      .ss-brand-badge {
        width: 24px; height: 24px; border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        font-size: 11px; font-weight: 700; color: #000;
      }
      .ss-brand-name { font-weight: 600; font-size: 15px; color: #e0e0e0; }
      .ss-period { margin-left: auto; font-size: 12px; color: #666; }
      .ss-kpi-row {
        display: flex; gap: 10px; margin-bottom: 16px;
      }
      .ss-kpi-card {
        flex: 1; background: #252525; border-radius: 6px;
        padding: 10px 12px; border: 1px solid #2e2e2e;
      }
      .ss-kpi-label { font-size: 11px; color: #888; margin-bottom: 4px; }
      .ss-kpi-value { font-size: 18px; font-weight: 700; color: #f0f0f0; }
      .ss-table {
        width: 100%; border-collapse: collapse; font-size: 13px;
      }
      .ss-table th {
        text-align: left; padding: 6px 8px;
        border-bottom: 1px solid #333; color: #888; font-weight: 500;
      }
      .ss-table th.num, .ss-table td.num { text-align: right; }
      .ss-table td {
        padding: 5px 8px; border-bottom: 1px solid #262626; color: #ccc;
      }
      .ss-table tr:hover td { background: #252525; }
      .ss-empty { color: #555; font-size: 13px; padding: 12px 0; }

      @media (max-width: 768px) {
        .ss-sections { flex-direction: column; }
        .ss-kpi-row { flex-wrap: wrap; }
        .ss-kpi-card { min-width: 80px; }
      }
    `;
    document.head.appendChild(s);
  }

  // ── export ────────────────────────────────────────────────
  injectStyles();
  global.SS = { init };

})(window);
