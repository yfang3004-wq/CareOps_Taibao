'use strict';
// 第 8 模块：长者适老微端（8.1）与家属安心透明端（8.2）
const CareOpsPortal = (() => {
  const P = {
    mode: 'elder', // 'elder' | 'family'
    elderTab: 'menu', // 'menu' | 'activities' | 'service'
    familyTab: 'billing', // 'billing' | 'activities' | 'care'
    residents: [],
    residentId: '',
    elderData: null,
    familyData: null,
    revision: 0,
    busy: false,
  };

  function reqId(prefix = 'PORTAL') {
    return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  }

  async function ensureResidents() {
    const res = await api('/portal/residents');
    P.revision = res.revision;
    P.residents = res.residents || [];
    if (!P.residentId || !P.residents.some(r => r.id === P.residentId)) {
      const preferred = P.mode === 'elder'
        ? P.residents.find(r => r.is_vitality) || P.residents[0]
        : P.residents.find(r => r.id === 'R005') || P.residents[0];
      P.residentId = preferred ? preferred.id : '';
    }
  }

  function renderElderPhone(d) {
    const r = d.resident;
    const tabs = [
      ['menu', '今日食谱'],
      ['activities', '社团报名'],
      ['service', '管家报修'],
    ];
    let body = '';
    if (P.elderTab === 'menu') {
      const menu = d.menu;
      body = `
        <article class="portal-card">
          <div class="portal-card-head">
            <h3>本周养生食谱</h3>
            <span class="portal-tag">${esc(menu ? menu.week_start + ' 周' : '待发布')}</span>
          </div>
          <p class="portal-body-text">${esc(menu ? menu.content : '营养厨房正在审定本周食谱，请稍后查看。')}</p>
          ${menu?.note ? `<p class="portal-meta">供餐说明：${esc(menu.note)}</p>` : ''}
        </article>
        <article class="portal-card">
          <div class="portal-card-head">
            <h3>今日出餐进度</h3>
            <span class="portal-tag">${d.today_meals.length} 批次</span>
          </div>
          ${d.today_meals.map(m => `
            <div class="portal-list-item">
              <p class="portal-body-text"><strong>${esc(m.meal)}</strong> · ${esc(m.title)}</p>
              <p class="portal-meta">厨房状态：${esc(m.status_label)}</p>
            </div>
          `).join('') || '<p class="portal-meta">今日餐次正在排期备餐。</p>'}
        </article>
        <article class="portal-card">
          <div class="portal-card-head">
            <h3>我的口味与忌口记录</h3>
          </div>
          ${d.diet_preferences.map(p => `
            <div class="portal-list-item">
              <p class="portal-body-text">${esc(p.content)}</p>
              <p class="portal-meta">${esc(p.source)} · ${esc(p.created_at)}</p>
            </div>
          `).join('') || '<p class="portal-meta">尚未登记特殊口味偏好。</p>'}
        </article>
        <button type="button" class="portal-primary-cta" data-action="portal-open-diet-modal">告诉食堂我的口味偏好</button>
      `;
    } else if (P.elderTab === 'activities') {
      body = `
        <article class="portal-card">
          <div class="portal-card-head">
            <h3>金陵社团与水疗课</h3>
            <span class="portal-tag">大字预报名</span>
          </div>
          ${d.activities.map(a => `
            <div class="portal-list-item">
              <p class="portal-body-text"><strong>${esc(a.title)}</strong></p>
              <p class="portal-meta">时间：${esc(a.scheduled_at)}<br>地点：${esc(a.location)} · 已报名 ${a.participant_count} 人</p>
              ${a.checked_in
                ? '<button type="button" class="portal-outline-btn" disabled>已现场签到参与</button>'
                : a.signed_up
                  ? '<button type="button" class="portal-outline-btn" disabled>已预报名（到场由社工签到）</button>'
                  : a.can_signup
                    ? `<button type="button" class="portal-outline-btn" data-action="portal-signup-activity" data-id="${esc(a.id)}">报名参加此活动</button>`
                    : '<button type="button" class="portal-outline-btn" disabled>活动已结束</button>'
              }
            </div>
          `).join('') || '<p class="portal-body-text">本周暂无新发布的社团课程。</p>'}
        </article>
        <button type="button" class="portal-primary-cta" data-action="portal-open-concierge-modal" data-repair="0">有事呼叫生活管家协助</button>
      `;
    } else {
      const stages = ['待处理', '处理中', '待验收', '已完成'];
      body = `
        <article class="portal-card">
          <div class="portal-card-head">
            <h3>居室报修与管家进度</h3>
            <span class="portal-tag">${d.concierge_requests.length} 条记录</span>
          </div>
          ${d.concierge_requests.map(c => `
            <div class="portal-list-item">
              <p class="portal-body-text"><strong>${esc(c.title)}</strong></p>
              <p class="portal-meta">${esc(c.date)} · ${esc(c.status_label)}</p>
              <p class="portal-body-text">${esc(c.request)}</p>
              ${c.repair ? `
                <div class="portal-stage-steps" aria-label="维修四步进度">
                  ${stages.map((st, idx) => `<span class="portal-stage-step ${idx + 1 <= c.repair.step_index ? 'done' : ''}">${st}</span>`).join('')}
                </div>
              ` : ''}
            </div>
          `).join('') || '<p class="portal-body-text">您的居室设施目前一切正常，暂无报修记录。</p>'}
        </article>
        <button type="button" class="portal-primary-cta" data-action="portal-open-concierge-modal" data-repair="1">一键呼叫管家 / 居室报修</button>
      `;
    }

    return `
      <div class="portal-phone-frame" role="region" aria-label="活力长者适老微端手机预览">
        <div class="portal-phone-notch">
          <span>栖云颐养 · 适老微端</span>
          <span>${esc(r.bed || '未分房')}</span>
        </div>
        <h2 class="portal-phone-title">${esc(r.label)}，您好</h2>
        <p class="portal-phone-subtitle">${esc(r.care_level)} · 在院状态：${esc(r.stay_status)}</p>
        <div class="portal-subtabs" role="tablist">
          ${tabs.map(([k, lbl]) => `<button type="button" class="portal-subtab ${P.elderTab === k ? 'active' : ''}" data-action="portal-elder-tab" data-tab="${k}">${lbl}</button>`).join('')}
        </div>
        ${body}
      </div>
    `;
  }

  function renderFamilyPhone(d) {
    const r = d.resident;
    const b = d.billing;
    const tabs = [
      ['billing', '透明账单'],
      ['activities', '实拍动态'],
      ['care', '安心回执'],
    ];
    let body = '';
    if (P.familyTab === 'billing') {
      body = `
        <article class="portal-card">
          <div class="portal-card-head">
            <h3>${esc(b.period)} 已核对账单</h3>
            <span class="portal-tag">财务已核验</span>
          </div>
          <div class="portal-fee-grid">
            <div class="portal-fee-box">
              <span class="portal-meta">本期应缴合计</span>
              <strong>¥${esc(b.due)}</strong>
            </div>
            <div class="portal-fee-box">
              <span class="portal-meta">已核销实收/抵扣</span>
              <strong>¥${esc(b.confirmed_paid)}</strong>
            </div>
            <div class="portal-fee-box">
              <span class="portal-meta">预交养老金余额</span>
              <strong>¥${esc(b.advance_balance)}</strong>
            </div>
            <div class="portal-fee-box">
              <span class="portal-meta">履约押金（独立隔离）</span>
              <strong>¥${esc(b.deposit_balance)}</strong>
            </div>
          </div>
          <p class="portal-meta">本期尚待核销余额：¥${esc(b.outstanding)}（押金严禁自动冲抵日常账单）</p>
          ${b.unverified_count > 0 ? `<p class="portal-meta" style="margin-top:8px;padding-top:8px;border-top:1px dashed #dfd6c8;">提示：另有 ${b.unverified_count} 笔家属申报款项（合计 ¥${esc(b.unverified_amount)}）待财务核对银行回单，核实前不计入已付金额。</p>` : ''}
        </article>
        <article class="portal-card">
          <div class="portal-card-head">
            <h3>收费与预交抵扣明细</h3>
          </div>
          ${b.charges.map(c => `
            <div class="portal-list-item">
              <p class="portal-body-text">${esc(c.description)} <strong>¥${esc(c.amount)}</strong></p>
              <p class="portal-meta">账期 ${esc(c.period)} · 登记于 ${esc(c.created_at)}</p>
            </div>
          `).join('')}
          ${b.confirmed_payments.map(p => `
            <div class="portal-list-item">
              <p class="portal-body-text">${esc(p.category_label)} <strong>¥${esc(p.amount)}</strong></p>
              <p class="portal-meta">凭证：${esc(p.reference)} · ${esc(p.confirmed_at)}</p>
            </div>
          `).join('')}
        </article>
        <button type="button" class="portal-primary-cta" data-action="portal-open-visit-modal">预约到院探视 / 外出报备</button>
      `;
    } else if (P.familyTab === 'activities') {
      body = `
        <article class="portal-card">
          <div class="portal-card-head">
            <h3>实到参与影像档案</h3>
            <span class="portal-tag">仅统计实到签到</span>
          </div>
          ${d.verified_activities.map(a => `
            <div class="portal-list-item">
              <p class="portal-body-text"><strong>${esc(a.title)}</strong></p>
              <p class="portal-meta">${esc(a.scheduled_at)} · ${esc(a.location)}</p>
              <p class="portal-body-text">${esc(a.summary)}</p>
              ${(a.attachments || []).map(att => `<img class="portal-photo" src="${esc(att.url)}" alt="${esc(a.title)}活动现场记录">`).join('')}
            </div>
          `).join('') || '<p class="portal-body-text">近期暂无已确认现场签到的活动影像（仅预报名未到场的活动不会在此冒充展示）。</p>'}
        </article>
        <button type="button" class="portal-primary-cta" data-action="portal-open-visit-modal">预约周末陪伴探视</button>
      `;
    } else {
      const cd = d.care_digest;
      body = `
        <article class="portal-card">
          <div class="portal-card-head">
            <h3>今日照护履约摘要</h3>
            <span class="portal-tag">${esc(cd.date)}</span>
          </div>
          <p class="portal-body-text">今日计划服务 ${cd.planned_total} 项：已完成 ${cd.completed_count} 项${cd.deferred_count ? `，尊重长者意愿改期 ${cd.deferred_count} 项` : ''}。</p>
          ${cd.completed_items.map(i => `<p class="portal-meta">已完成：${esc(i.slot)} ${esc(i.title)}（${esc(i.performed_at)}）</p>`).join('')}
          ${cd.deferred_items.map(i => `<p class="portal-meta">温情改期：${esc(i.slot)} ${esc(i.title)}（原因：${esc(i.reason)}，${esc(i.note)}）</p>`).join('')}
        </article>
        <article class="portal-card">
          <div class="portal-card-head">
            <h3>异常处置闭环安心回执</h3>
            <span class="portal-tag">已核验脱敏</span>
          </div>
          ${d.closed_receipts.map(rc => `
            <div class="portal-list-item">
              <p class="portal-body-text"><strong>${esc(rc.type)}</strong></p>
              <p class="portal-meta">${esc(rc.source_label)} · 闭环于 ${esc(rc.handled_at)}</p>
              <p class="portal-body-text">${esc(rc.summary)}</p>
            </div>
          `).join('') || '<p class="portal-body-text">近期平稳，无需要通报的异常处置事项。</p>'}
        </article>
        ${d.visit_records.length ? `
          <article class="portal-card">
            <div class="portal-card-head"><h3>我的探视预约记录</h3></div>
            ${d.visit_records.map(v => `<div class="portal-list-item"><p class="portal-body-text">${esc(v.title)}</p><p class="portal-meta">${esc(v.status_label)} · ${esc(v.request)}</p></div>`).join('')}
          </article>
        ` : ''}
        <button type="button" class="portal-primary-cta" data-action="portal-open-visit-modal">提交探视或陪同外出申请</button>
      `;
    }

    return `
      <div class="portal-phone-frame" role="region" aria-label="家属安心透明端手机预览">
        <div class="portal-phone-notch">
          <span>栖云颐养 · 家属安心端</span>
          <span>长者状态：${esc(r.stay_status)}</span>
        </div>
        <h2 class="portal-phone-title">${esc(r.label)}的家属专页</h2>
        <p class="portal-phone-subtitle">绑定监护人：${esc(r.guardian_name)} · 房床：${esc(r.bed)}</p>
        <div class="portal-subtabs" role="tablist">
          ${tabs.map(([k, lbl]) => `<button type="button" class="portal-subtab ${P.familyTab === k ? 'active' : ''}" data-action="portal-family-tab" data-tab="${k}">${lbl}</button>`).join('')}
        </div>
        ${body}
      </div>
    `;
  }

  async function render() {
    const content = $('content');
    if (!content) return;
    content.innerHTML = '<p class="loading">正在同步长者与家属轻协同端脱敏视图…</p>';
    await ensureResidents();
    if (!P.residentId) {
      content.innerHTML = '<section class="panel"><h2>长者与家属轻协同端</h2><p class="empty">当前尚无有效在院或保留床位长者。</p></section>';
      return;
    }
    let phoneHtml = '';
    if (P.mode === 'elder') {
      const data = await api('/portal/elder?resident_id=' + encodeURIComponent(P.residentId));
      P.revision = data.revision;
      P.elderData = data;
      phoneHtml = renderElderPhone(data);
    } else {
      const data = await api('/portal/family?resident_id=' + encodeURIComponent(P.residentId));
      P.revision = data.revision;
      P.familyData = data;
      phoneHtml = renderFamilyPhone(data);
    }

    content.innerHTML = `
      <div class="portal-control-bar">
        <div class="portal-control-group">
          <div class="portal-mode-switch" role="group" aria-label="切换长者端或家属端">
            <button type="button" class="${P.mode === 'elder' ? 'active' : ''}" data-action="portal-switch-mode" data-mode="elder">8.1 长者适老微端</button>
            <button type="button" class="${P.mode === 'family' ? 'active' : ''}" data-action="portal-switch-mode" data-mode="family">8.2 家属安心透明端</button>
          </div>
          <label style="margin:0;display:inline-flex;align-items:center;gap:8px;font-weight:560;">
            当前长者
            <select id="portal-resident-select" style="min-width:220px;">
              ${P.residents.map(r => `<option value="${esc(r.id)}" ${r.id === P.residentId ? 'selected' : ''}>${esc(r.label)} · ${esc(r.bed)}（${esc(r.care_level)}）</option>`).join('')}
            </select>
          </label>
        </div>
        <div class="actions ui-toolbar" role="toolbar" focusgroup="toolbar wrap" aria-label="协同端同步操作">
          <div class="ui-btn-group" role="group" aria-label="版本与刷新">
            <span class="chip">v${esc(P.revision)}</span>
            ${btn('portal-refresh', '刷新同步', '', 'small')}
          </div>
        </div>
      </div>
      <div class="portal-stage">
        ${phoneHtml}
        <aside class="portal-inspector">
          <div class="panel-head">
            <h2>院内外同库协同与隐私隔离</h2>
            <span class="chip">390px 适老真机视口</span>
          </div>
          <div class="portal-sync-grid">
            <div class="portal-sync-card">
              <strong>费用核验与押金隔离</strong>
              <p>家属口头申报款项未核对前不计入实收；履约押金与预交款分池隔离。</p>
            </div>
            <div class="portal-sync-card">
              <strong>实到签到影像准入</strong>
              <p>微端预报名仅进意向名单，须经活动社工现场签到确认后才同步家属端影像。</p>
            </div>
            <div class="portal-sync-card">
              <strong>脱敏安心回执</strong>
              <p>仅展示护士站或医务室闭环回执，剥离内部交班草稿与认知筛查量表全文。</p>
            </div>
            <div class="portal-sync-card">
              <strong>管家工程一键直连</strong>
              <p>居室报修同步在生活管家建单并派发工程四状态维修单，直推院长督办台。</p>
            </div>
          </div>
          <div class="actions ui-toolbar" role="toolbar" focusgroup="toolbar wrap" aria-label="院内部门核验跳转">
            <div class="ui-btn-group" role="group" aria-label="跳转院内工作台">
              ${btn('dept-nav', '销售核费', 'data-page="dept-sales"', 'small')}
              ${btn('dept-nav', '活动签到', 'data-page="dept-activities"', 'small')}
              ${btn('dept-nav', '护理交班 / 管家', 'data-page="dept-care"', 'small')}
            </div>
          </div>
        </aside>
      </div>
    `;
    const sel = $('portal-resident-select');
    if (sel) {
      sel.onchange = async () => {
        P.residentId = sel.value;
        await render();
      };
    }
  }

  async function handleClick(b) {
    const act = b.dataset.action;
    if (!act || !act.startsWith('portal-')) return false;
    if (P.busy) {
      toast('正在提交，请稍候…');
      return true;
    }
    if (act === 'portal-open') {
      S.departmentMaintenance = false;
      S.page = 'dept-portal';
      await loadPage();
      return true;
    }
    if (act === 'portal-switch-mode') {
      P.mode = b.dataset.mode;
      await render();
      return true;
    }
    if (act === 'portal-elder-tab') {
      P.elderTab = b.dataset.tab;
      await render();
      return true;
    }
    if (act === 'portal-family-tab') {
      P.familyTab = b.dataset.tab;
      await render();
      return true;
    }
    if (act === 'portal-refresh') {
      await render();
      toast('已同步最新院内数据');
      return true;
    }
    if (act === 'portal-signup-activity') {
      P.busy = true;
      try {
        const res = await api('/portal/elder/action', 'POST', {
          request_id: reqId('SIGNUP'),
          expected_workspace_revision: P.revision,
          resident_id: P.residentId,
          action: 'signup_activity',
          values: { activity_id: b.dataset.id },
        });
        P.revision = res.revision;
        toast('预报名成功！到场后由活动社工为您确认签到。');
        await render();
      } finally {
        P.busy = false;
      }
      return true;
    }
    if (act === 'portal-open-diet-modal') {
      dialog('登记口味与饮食偏好', `
        <form id="portal-diet-form" class="portal-form">
          <label>您希望食堂注意哪些口味或软硬偏好？
            <textarea name="content" rows="3" required maxlength="300" placeholder="例如：晚餐希望米饭偏软烂、少盐不放葱蒜"></textarea>
          </label>
          <p class="error" id="form-error" role="alert" tabindex="-1"></p>
          <div class="form-footer">
            ${btn('close-dialog', '取消')}
            <button type="submit" class="primary">提交给营养食堂</button>
          </div>
        </form>
      `);
      return true;
    }
    if (act === 'portal-open-concierge-modal') {
      const repair = b.dataset.repair === '1';
      dialog(repair ? '居室报修与呼叫管家' : '呼叫生活管家协助', `
        <form id="portal-concierge-form" class="portal-form" data-repair="${repair ? '1' : '0'}">
          <label>${repair ? '请描述房间内需要检修的设施与方便入室的时间' : '请填写您需要生活管家协助的日常事项'}
            <textarea name="request" rows="3" required maxlength="400" placeholder="${repair ? '例如：卫生间扶手旁夜灯不亮，下午三点我在茶社时可入室检修' : '例如：请管家明天上午帮忙代收快递并送到201房'}"></textarea>
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-size:17px;">
            <input type="checkbox" name="need_repair" value="1" ${repair ? 'checked' : ''} style="width:20px;height:20px;margin:0;">
            同步生成工程部居室维修工单（自动脱敏隐藏健康资料）
          </label>
          <p class="error" id="form-error" role="alert" tabindex="-1"></p>
          <div class="form-footer">
            ${btn('close-dialog', '取消')}
            <button type="submit" class="primary">确认发送</button>
          </div>
        </form>
      `);
      return true;
    }
    if (act === 'portal-open-visit-modal') {
      const today = new Date().toISOString().slice(0, 10);
      const defVisitor = P.familyData?.resident?.guardian_name || '';
      dialog('家属到院探视 / 陪同外出预约', `
        <form id="portal-visit-form" class="portal-form">
          <label>计划到访日期
            <input type="date" name="visit_date" value="${esc(today)}" required>
          </label>
          <label>到访家属姓名
            <input type="text" name="visitor_name" value="${esc(defVisitor)}" required maxlength="80">
          </label>
          <label>探视时段或陪同外出说明（可选）
            <textarea name="note" rows="2" maxlength="400" placeholder="例如：周六上午10:00到院探望父母，中午预约康养餐厅家庭包间同餐"></textarea>
          </label>
          <p class="error" id="form-error" role="alert" tabindex="-1"></p>
          <div class="form-footer">
            ${btn('close-dialog', '取消')}
            <button type="submit" class="primary">提交生活管家安排</button>
          </div>
        </form>
      `);
      return true;
    }
    return false;
  }

  async function handleSubmit(form) {
    if (!['portal-diet-form', 'portal-concierge-form', 'portal-visit-form'].includes(form.id)) return false;
    if (P.busy) return true;
    P.busy = true;
    try {
      const fd = new FormData(form);
      if (form.id === 'portal-diet-form') {
        const res = await api('/portal/elder/action', 'POST', {
          request_id: reqId('DIET'),
          expected_workspace_revision: P.revision,
          resident_id: P.residentId,
          action: 'submit_diet_preference',
          values: { content: String(fd.get('content') || '') },
        });
        P.revision = res.revision;
        $('dialog').close();
        toast('口味偏好已写入共享主档，后勤膳食与护士站同步可见。');
        await render();
      } else if (form.id === 'portal-concierge-form') {
        const needRepair = fd.get('need_repair') === '1';
        const res = await api('/portal/elder/action', 'POST', {
          request_id: reqId('CONC'),
          expected_workspace_revision: P.revision,
          resident_id: P.residentId,
          action: 'submit_concierge_request',
          values: { request: String(fd.get('request') || ''), need_repair: needRepair },
        });
        P.revision = res.revision;
        $('dialog').close();
        P.elderTab = 'service';
        toast(needRepair ? '已呼叫管家并同步派发工程四状态维修单！' : '已发送至生活管家工作台！');
        await render();
      } else if (form.id === 'portal-visit-form') {
        const res = await api('/portal/family/visit', 'POST', {
          request_id: reqId('VISIT'),
          expected_workspace_revision: P.revision,
          resident_id: P.residentId,
          visit_date: String(fd.get('visit_date') || ''),
          visitor_name: String(fd.get('visitor_name') || ''),
          note: String(fd.get('note') || ''),
        });
        P.revision = res.revision;
        $('dialog').close();
        P.familyTab = 'care';
        toast('探视预约已提交至生活管家协同台！');
        await render();
      }
    } catch (err) {
      showFormError(err, form.querySelector('#form-error'));
    } finally {
      P.busy = false;
    }
    return true;
  }

  function reset() {
    P.elderData = null;
    P.familyData = null;
    P.busy = false;
  }

  return { render, handleClick, handleSubmit, reset };
})();
