(function () {
  const exactMap = new Map([
    ['Chart', '图表'],
    ['Date From', '开始日期'],
    ['Date To', '结束日期'],
    ['Ignore Naps', '忽略小睡'],
    ['Profile', '档案'],
    ['Load Sleep CSV', '加载睡眠 CSV'],
    ['Load HRV CSV', '加载 HRV CSV'],
    ['Load Activity CSV', '加载活动 CSV'],
    ['Load RHR CSV', '加载静息心率 CSV'],
    ['Steps & Activity View', '步数与活动视图'],
    ['RHR View', '静息心率视图'],
    ['Sleep View', '睡眠视图'],
    ['Minutes Asleep View', '睡眠时长视图'],
    ['Daily', '按日'],
    ['Monthly', '按月'],
    ['Yearly', '按年'],
    ['No file chosen', '未选择文件'],
    ['Profile Management', '档案管理'],
    ['New Profile', '新建档案'],
    ['Existing Profiles', '现有档案'],
    ['Create New Profile', '创建新档案'],
    ['Create Profile', '创建档案'],
    ['Manage Existing Profiles', '管理现有档案'],
    ['Authorize Profile', '授权档案'],
    ['Delete Profile', '删除档案'],
    ['Please Confirm', '请确认'],
    ['Okay', '好的'],
    ['Cancel', '取消'],
    ['Delete', '删除'],
    ['Submit', '提交'],
    ['Auth', '授权'],
    ['Delete Profile', '删除档案'],
    ['Loading profiles...', '正在加载档案...'],
    ['No profiles found', '未找到档案'],
    ['No Profiles', '没有档案'],
    ['No Profile', '没有档案'],
    ['No Second Profile', '没有第二个档案'],
    ['Predictions & Family Insights', '预测与家庭洞察'],
    ['Life Events', '生活事件'],
    ['Correlation Matrix', '相关矩阵'],
    ['📊 Comprehensive Analytics', '📊 综合分析'],
    ['💤 Sleep Score', '💤 睡眠分数'],
    ['Minutes Asleep', '睡眠时长'],
    ['Monthly Stage % (Deep/REM/Light)', '月度睡眠阶段占比（深睡 / REM / 浅睡）'],
    ['📉 HRV: Heatmap, CUSUM, Min/Max', '📉 HRV：热力图 / CUSUM / 极值'],
    ['Correlation: Sleep Score vs HRV', '相关性：睡眠分数 vs HRV'],
    ['👟 Steps & Activity', '👟 步数与活动'],
    ['Correlation: Steps vs HRV & Sleep Score', '相关性：步数 vs HRV / 睡眠分数'],
    ['❤️ RHR', '❤️ 静息心率'],
    ['RHR: Histogram, CUSUM, Min/Max', 'RHR：分布 / CUSUM / 极值'],
    ['Analytics', '综合分析'],
    ['Overview', '总览'],
    ['Profile 1', '档案 1'],
    ['Profile 2', '档案 2'],
    ['Fetching...', '同步中...'],
    ['Queued...', '已排队...'],
    ['Downloading...', '下载中...'],
    ['Creating...', '创建中...'],
    ['Authorizing...', '授权中...'],
    ['Deleting...', '删除中...'],
    ['Load More', '加载更多'],
    ['Download Monthly Summary', '下载月度摘要'],
    ['Download Yearly Summary', '下载年度摘要'],
    ['Download Analytics Summary', '下载综合分析摘要'],
    ['Data Preview', '数据预览'],
    ['Fetch New Data', '同步最新数据'],
    ['Sync Fitbit Data', '同步 Fitbit 数据'],
    ['Show:', '显示：'],
    ['Prev', '上一页'],
    ['Next', '下一页'],
    ['All', '全部'],
    ['Walk', '步行'],
    ['Run', '跑步'],
    ['Bike', '骑行'],
    ['Swim', '游泳'],
    ['Workout', '训练'],
    ['Treadmill', '跑步机'],
  ]);

  const partialReplacements = [
    ['Create Profile', '创建档案'],
    ['Profile Name', '档案名称'],
    ['Client ID', 'Client ID'],
    ['Client Secret', 'Client Secret'],
    ['Loading', '加载中'],
    ['No file chosen', '未选择文件'],
    ['Authorization complete!', '授权完成！'],
    ['Fetching data', '正在同步数据'],
    ['Fetch complete', '同步完成'],
    ['Fetch failed', '同步失败'],
    ['user authorized - press ↻ to fetch data!', '已完成授权 - 点 ↻ 开始同步数据！'],
    ['Go to Profile Management -> New Profile', '请前往：档案管理 -> 新建档案'],
    ['Go to Profile Management -> Existing Profiles -> Auth', '请前往：档案管理 -> 现有档案 -> 授权'],
    ['Current status:', '当前状态：'],
    ['Please load', '请先加载'],
    ['sleep score', '睡眠分数'],
    ['steps days', '步数天数'],
    ['RHR days', 'RHR 天数'],
    ['nights', '晚'],
    ['range', '范围'],
    ['avg', '均值'],
    ['points', '点'],
  ];

  function translateString(input) {
    if (!input || typeof input !== 'string') return input;
    const raw = input;
    const trimmed = raw.trim();
    if (!trimmed) return raw;
    if (exactMap.has(trimmed)) {
      return raw.replace(trimmed, exactMap.get(trimmed));
    }
    let output = raw;
    for (const [from, to] of partialReplacements) {
      if (output.includes(from)) output = output.split(from).join(to);
    }
    return output;
  }

  function translateNodeText(node) {
    if (!node || !node.nodeValue) return;
    const parent = node.parentElement;
    if (!parent) return;
    const tag = parent.tagName;
    if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'CODE') return;
    const translated = translateString(node.nodeValue);
    if (translated !== node.nodeValue) node.nodeValue = translated;
  }

  function translateAttributes(el) {
    ['placeholder', 'title', 'aria-label', 'value'].forEach((attr) => {
      if (!el.hasAttribute || !el.hasAttribute(attr)) return;
      const current = el.getAttribute(attr);
      const translated = translateString(current);
      if (translated !== current) el.setAttribute(attr, translated);
    });
  }

  function translateTree(root) {
    if (!root) return;
    if (root.nodeType === Node.TEXT_NODE) {
      translateNodeText(root);
      return;
    }
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;

    if (root.nodeType === Node.ELEMENT_NODE) {
      translateAttributes(root);
      if (root.tagName === 'OPTION' && root.textContent) {
        const translated = translateString(root.textContent);
        if (translated !== root.textContent) root.textContent = translated;
      }
    }

    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    const textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);
    textNodes.forEach(translateNodeText);

    if (root.querySelectorAll) {
      root.querySelectorAll('*').forEach((el) => {
        translateAttributes(el);
        if (el.tagName === 'OPTION' && el.textContent) {
          const translated = translateString(el.textContent);
          if (translated !== el.textContent) el.textContent = translated;
        }
      });
    }

    document.documentElement.lang = 'zh-CN';
  }

  function applyTranslations() {
    translateTree(document.body || document.documentElement);
  }

  document.addEventListener('DOMContentLoaded', applyTranslations);
  window.addEventListener('load', applyTranslations);

  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      if (mutation.type === 'characterData') {
        translateNodeText(mutation.target);
        return;
      }
      mutation.addedNodes.forEach((node) => translateTree(node));
      if (mutation.target && mutation.target.nodeType === Node.ELEMENT_NODE) {
        translateAttributes(mutation.target);
      }
    });
  });

  if (document.documentElement) {
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ['placeholder', 'title', 'aria-label', 'value'],
    });
  }
})();
