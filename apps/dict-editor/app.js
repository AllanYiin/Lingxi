import {
  EMOTION_OPTIONS,
  ENTITY_OPTIONS,
  TAG_OPTIONS,
  DEFAULT_NEW_ENTRY_FREQUENCY,
  applyDictionaryReplacement,
  filterAndSortRows,
  inferPotentialWord,
  parseDictionaryText,
  parseTrainingText,
  previewDictionaryReplacement,
  replaceTrainingLines,
  serializeDictionaryRows,
  parseAffectLexiconText,
  parseCustomLexiconText,
  parseEmotionTaxonomyText,
  previewLegacyEmotionMigration,
  derivePolarity,
  serializeAffectLexicon,
  serializeCustomLexicon
} from "./core.js";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const numberFormat = new Intl.NumberFormat("zh-TW");

const state = {
  mode: "dictionary",
  busy: false,
  dictionary: {
    rows: [],
    filteredRows: [],
    rowById: new Map(),
    fileHandle: null,
    fileName: "",
    dirty: false,
    warnings: [],
    selected: new Set(),
    page: 1,
    pageSize: 50,
    sort: { key: "source", direction: "asc" },
    nextId: 1,
    editingId: null
  },
  custom: {
    data: null,
    fileHandle: null,
    fileName: "",
    dirty: false
  },
  affect: {
    data: null,
    taxonomy: null,
    editingIndex: null,
    fileHandle: null,
    fileName: "",
    dirty: false,
    filters: { query: "", family: "", emotion: "", polarity: "", status: "" }
  },
  training: {
    lines: [],
    fileHandle: null,
    fileName: "",
    dirty: false,
    viewItems: [],
    viewLabel: "",
    startIndex: 0,
    selectedIndex: null,
    newWords: new Set()
  },
  replaceMode: "dictionary",
  replacePreview: null
};

const elements = {
  fileState: $("#file-state"),
  fileName: $("#file-name"),
  fileMeta: $("#file-meta"),
  openButton: $("#open-file-button"),
  saveButton: $("#save-file-button"),
  alert: $("#app-alert"),
  alertTitle: $("#alert-title"),
  alertMessage: $("#alert-message"),
  dictionaryWorkspace: $("#dictionary-workspace"),
  customWorkspace: $("#custom-workspace"),
  affectWorkspace: $("#affect-workspace"),
  trainingWorkspace: $("#training-workspace"),
  dictionaryEmpty: $("#dictionary-empty"),
  dictionaryEditor: $("#dictionary-editor"),
  trainingEmpty: $("#training-empty"),
  trainingEditor: $("#training-editor"),
  customEmpty: $("#custom-empty"),
  customEditor: $("#custom-editor"),
  customId: $("#custom-id"),
  customDomain: $("#custom-domain"),
  customPriority: $("#custom-priority"),
  customEnabled: $("#custom-enabled"),
  customEntries: $("#custom-entries"),
  customSummary: $("#custom-summary"),
  affectEmpty: $("#affect-empty"),
  affectEditor: $("#affect-editor"),
  affectEntrySelect: $("#affect-entry-select"),
  affectWord: $("#affect-word"),
  affectSource: $("#affect-source"),
  affectContext: $("#affect-context"),
  affectPolarity: $("#affect-polarity"),
  affectPolarityResult: $("#affect-polarity-result"),
  affectTree: $("#affect-emotion-tree"),
  affectNotes: $("#affect-notes"),
  affectSummary: $("#affect-summary"),
  affectFilterQuery: $("#affect-filter-query"),
  affectFilterFamily: $("#affect-filter-family"),
  affectFilterEmotion: $("#affect-filter-emotion"),
  affectFilterPolarity: $("#affect-filter-polarity"),
  affectFilterStatus: $("#affect-filter-status"),
  dictionaryBody: $("#dictionary-table-body"),
  tableEmpty: $("#table-empty"),
  dictionaryQuery: $("#dictionary-query"),
  filterPanel: $("#dictionary-filters"),
  filterTag: $("#filter-tag"),
  filterEntity: $("#filter-entity"),
  filterEmotion: $("#filter-emotion"),
  filterMin: $("#filter-min-frequency"),
  filterMax: $("#filter-max-frequency"),
  filterCount: $("#filter-count"),
  selectPage: $("#select-page-checkbox"),
  resultSummary: $("#dictionary-result-summary"),
  pageIndicator: $("#page-indicator"),
  pageSize: $("#page-size-select"),
  firstPage: $("#first-page-button"),
  previousPage: $("#previous-page-button"),
  nextPage: $("#next-page-button"),
  lastPage: $("#last-page-button"),
  selectionBar: $("#selection-bar"),
  selectionCount: $("#selection-count"),
  entryDialog: $("#entry-dialog"),
  entryForm: $("#entry-form"),
  entryTitle: $("#entry-dialog-title"),
  entryWord: $("#entry-word"),
  entryTag: $("#entry-tag"),

  entryEntity: $("#entry-entity"),
  entryEmotion: $("#entry-emotion"),
  entryError: $("#entry-form-error"),
  entrySubmit: $("#entry-submit-button"),
  replaceDialog: $("#replace-dialog"),
  replaceForm: $("#replace-form"),
  replaceTitle: $("#replace-dialog-title"),
  replaceBefore: $("#replace-before"),
  replaceAfter: $("#replace-after"),
  replacePreview: $("#replace-preview"),
  replaceSubmit: $("#replace-submit-button"),
  collectNewWordField: $("#collect-new-word-field"),
  collectNewWord: $("#collect-new-word-checkbox"),
  confirmDialog: $("#confirm-dialog"),
  confirmTitle: $("#confirm-title"),
  confirmMessage: $("#confirm-message"),
  confirmSubmit: $("#confirm-submit-button"),
  corpusViewer: $("#corpus-viewer"),
  trainingQuery: $("#training-query"),
  trainingViewLabel: $("#training-view-label"),
  trainingStartIndex: $("#training-start-index"),
  trainingSummary: $("#training-result-summary"),
  deleteTrainingLine: $("#delete-training-line-button"),
  newWordDrawer: $("#new-word-drawer"),
  drawerBackdrop: $("#drawer-backdrop"),
  newWordList: $("#new-word-list"),
  newWordCount: $("#new-word-count"),
  openNewWords: $("#open-new-words-button"),
  loadingLayer: $("#loading-layer"),
  loadingTitle: $("#loading-title"),
  loadingMessage: $("#loading-message"),
  toastRegion: $("#toast-region"),
  dictionaryFileInput: $("#dictionary-file-input"),
  trainingFileInput: $("#training-file-input")
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function optionMarkup(options, selected, includeBlank = false, blankLabel = "全部") {
  const known = new Set(options.map(([code]) => code));
  const custom =
    selected && !known.has(selected) ? [[selected, "檔案中的自訂值"]] : [];
  const blank = includeBlank ? `<option value="">${escapeHtml(blankLabel)}</option>` : "";
  return (
    blank +
    [...custom, ...options]
      .map(
        ([code, label]) =>
          `<option value="${escapeHtml(code)}"${code === selected ? " selected" : ""}>${escapeHtml(
            code
          )} · ${escapeHtml(label)}</option>`
      )
      .join("")
  );
}

function populateStaticOptions() {
  elements.filterTag.innerHTML = optionMarkup(TAG_OPTIONS, "", true, "全部詞性");
  elements.filterEntity.innerHTML = optionMarkup(
    ENTITY_OPTIONS.filter(([code]) => code !== "None"),
    "",
    true,
    "全部實體"
  );
  elements.filterEmotion.innerHTML = optionMarkup(
    EMOTION_OPTIONS.filter(([code]) => code !== "None"),
    "",
    true,
    "全部情感"
  );
  elements.entryTag.innerHTML = optionMarkup(TAG_OPTIONS, "unknownnew");
  elements.entryEntity.innerHTML = optionMarkup(ENTITY_OPTIONS, "None");
  elements.entryEmotion.innerHTML = optionMarkup(EMOTION_OPTIONS, "None");
}

function nextPaint() {
  return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
}

function debounce(callback, delay = 160) {
  let timeout;
  return (...args) => {
    window.clearTimeout(timeout);
    timeout = window.setTimeout(() => callback(...args), delay);
  };
}

function currentStore() {
  return state[state.mode];
}

function hasLoadedFile(mode = state.mode) {
  if (mode === "dictionary") {
    return state.dictionary.rows.length > 0 || Boolean(state.dictionary.fileName);
  }
  if (mode === "training") {
    return state.training.lines.length > 0 || Boolean(state.training.fileName);
  }
  return Boolean(state[mode].data);
}

function updateChrome() {
  const store = currentStore();
  const loaded = hasLoadedFile();
  const count =
    state.mode === "dictionary"
      ? state.dictionary.rows.length
      : state.mode === "training"
        ? state.training.lines.length
        : state[state.mode].data?.entries?.length ?? 0;
  const noun = state.mode === "training" ? "筆資料" : "筆詞條";

  elements.fileState.classList.toggle("is-ready", loaded && !store.dirty);
  elements.fileState.classList.toggle("is-dirty", loaded && store.dirty);
  elements.fileState.classList.remove("is-error");
  elements.fileName.textContent = loaded ? store.fileName : "尚未開啟檔案";
  elements.fileMeta.textContent = loaded
    ? `${numberFormat.format(count)} ${noun}${store.dirty ? " · 有未儲存變更" : " · 已同步"}`
    : "所有資料只在本機處理";
  const openLabels = {
    dictionary: "開啟詞典",
    custom: "開啟自訂辭典",
    affect: "開啟情感詞典",
    training: "開啟訓練資料"
  };
  elements.openButton.querySelector("span").textContent = openLabels[state.mode];
  elements.saveButton.disabled = !loaded || !store.dirty || state.busy;

  elements.dictionaryEmpty.hidden = hasLoadedFile("dictionary");
  elements.dictionaryEditor.hidden = !hasLoadedFile("dictionary");
  elements.trainingEmpty.hidden = hasLoadedFile("training");
  elements.trainingEditor.hidden = !hasLoadedFile("training");
  elements.customEmpty.hidden = hasLoadedFile("custom");
  elements.customEditor.hidden = !hasLoadedFile("custom");
  elements.affectEmpty.hidden = hasLoadedFile("affect");
  elements.affectEditor.hidden = !hasLoadedFile("affect");
}

function setMode(mode) {
  if (!["dictionary", "custom", "affect", "training"].includes(mode) || state.mode === mode) return;
  state.mode = mode;
  $$(".mode-switch__button").forEach((button) => {
    const active = button.dataset.mode === mode;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  for (const [name, workspace] of Object.entries({
    dictionary: elements.dictionaryWorkspace,
    custom: elements.customWorkspace,
    affect: elements.affectWorkspace,
    training: elements.trainingWorkspace
  })) {
    workspace.hidden = name !== mode;
    workspace.classList.toggle("is-active", name === mode);
  }
  elements.selectionBar.hidden = mode !== "dictionary" || state.dictionary.selected.size === 0;
  hideAlert();
  updateChrome();
}

function showAlert(title, message) {
  elements.alertTitle.textContent = title;
  elements.alertMessage.textContent = message;
  elements.alert.hidden = false;
  elements.fileState.classList.add("is-error");
}

function hideAlert() {
  elements.alert.hidden = true;
  elements.fileState.classList.remove("is-error");
}

function showLoading(title, message) {
  state.busy = true;
  elements.loadingTitle.textContent = title;
  elements.loadingMessage.textContent = message;
  elements.loadingLayer.hidden = false;
  updateChrome();
}

function hideLoading() {
  state.busy = false;
  elements.loadingLayer.hidden = true;
  updateChrome();
}

function toast(title, message = "", type = "success") {
  const item = document.createElement("div");
  item.className = `toast${type === "error" ? " is-error" : ""}`;
  item.innerHTML = `<strong>${escapeHtml(title)}</strong>${
    message ? `<span>${escapeHtml(message)}</span>` : ""
  }`;
  elements.toastRegion.append(item);
  window.setTimeout(() => item.remove(), 4200);
}

function markDirty(mode = state.mode) {
  state[mode].dirty = true;
  updateChrome();
}

async function fallbackFileInput(input) {
  return new Promise((resolve) => {
    input.value = "";
    input.onchange = () => resolve(input.files?.[0] ? { file: input.files[0], handle: null } : null);
    input.click();
  });
}

async function pickLocalFile(kind) {
  const dictionary = kind !== "training";
  if ("showOpenFilePicker" in window && window.isSecureContext) {
    try {
      const [handle] = await window.showOpenFilePicker({
        multiple: false,
        types: [
          dictionary
            ? {
                description: "LingXi JSON 詞典",
                accept: { "application/json": [".json"] }
              }
            : {
                description: "UTF-8 訓練資料",
                accept: { "text/plain": [".txt"] }
              }
        ]
      });
      return { file: await handle.getFile(), handle };
    } catch (error) {
      if (error.name === "AbortError") return null;
      throw error;
    }
  }
  return fallbackFileInput(
    dictionary ? elements.dictionaryFileInput : elements.trainingFileInput
  );
}

async function openCurrentFile() {
  try {
    const picked = await pickLocalFile(state.mode);
    if (!picked) return;
    if (state.mode === "dictionary") await loadDictionaryFile(picked.file, picked.handle);
    else if (state.mode === "custom") await loadCustomFile(picked.file, picked.handle);
    else if (state.mode === "affect") await loadAffectFile(picked.file, picked.handle);
    else await loadTrainingFile(picked.file, picked.handle);
  } catch (error) {
    showAlert("無法開啟檔案", `${error.message}。請確認瀏覽器權限後重新選取檔案。`);
  }
}

async function loadDictionaryFile(file, handle = null) {
  showLoading("正在解析詞典", `${file.name} · 大型詞典可能需要幾秒鐘`);
  hideAlert();
  await nextPaint();
  try {
    const parsed = parseDictionaryText(await file.text());
    const dict = state.dictionary;
    dict.rows = parsed.rows;
    dict.rowById = new Map(parsed.rows.map((row) => [row.id, row]));
    dict.filteredRows = [];
    dict.fileHandle = handle;
    dict.fileName = file.name;
    dict.dirty = false;
    dict.warnings = parsed.warnings;
    dict.selected.clear();
    dict.page = 1;
    dict.nextId = parsed.rows.length + 1;
    resetDictionaryFilters(false);
    applyDictionaryFilters();
    if (parsed.warnings.length) {
      showAlert(
        "部分詞條未載入",
        `共有 ${numberFormat.format(parsed.warnings.length)} 筆格式異常；第一筆：${
          parsed.warnings[0]
        }`
      );
    }
    toast("詞典已載入", `${numberFormat.format(parsed.rows.length)} 筆詞條可供編輯`);
  } catch (error) {
    showAlert("詞典格式無法解析", `${error.message}。請修正 JSON 後重新開啟。`);
    throw error;
  } finally {
    hideLoading();
  }
}


function renderCustomForm() {
  const data = state.custom.data;
  if (!data) return;
  elements.customId.value = data.id;
  elements.customDomain.value = data.domain;
  elements.customPriority.value = data.priority;
  elements.customEnabled.checked = data.enabled;
  elements.customEntries.value = data.entries
    .map((entry) => `${entry.word}\t${entry.pos ?? ""}`)
    .join("\n");
  elements.customSummary.textContent = `${numberFormat.format(data.entries.length)} 筆詞條 · 不含詞頻`;
}

function readCustomForm() {
  const existing = new Map(
    (state.custom.data?.entries ?? []).map((entry) => [entry.word, entry])
  );
  const entries = elements.customEntries.value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [word, pos] = line.split("\t", 2);
      const normalizedWord = word.trim();
      const previous = existing.get(normalizedWord);
      return {
        word: normalizedWord,
        ...(pos?.trim() ? { pos: pos.trim() } : {}),
        ...(previous?.affect ? { affect: structuredClone(previous.affect) } : {})
      };
    });
  const data = parseCustomLexiconText(JSON.stringify({
    schemaVersion: 1,
    id: elements.customId.value,
    domain: elements.customDomain.value,
    priority: Number(elements.customPriority.value || 0),
    enabled: elements.customEnabled.checked,
    entries
  }));
  state.custom.data = data;
  elements.customSummary.textContent = `${numberFormat.format(entries.length)} 筆詞條 · 不含詞頻`;
  return data;
}

async function loadCustomFile(file, handle = null) {
  showLoading("正在解析自訂辭典", file.name);
  try {
    const data = parseCustomLexiconText(await file.text());
    Object.assign(state.custom, {
      data,
      fileHandle: handle,
      fileName: file.name,
      dirty: false
    });
    renderCustomForm();
    toast("自訂辭典已載入", `${data.domain} · ${data.entries.length} 筆詞條`);
  } finally {
    hideLoading();
  }
}

async function ensureAffectTaxonomy() {
  if (state.affect.taxonomy) return state.affect.taxonomy;
  const response = await fetch("./emotion-taxonomy.json");
  if (!response.ok) throw new Error("無法載入 emotion-taxonomy.json");
  state.affect.taxonomy = parseEmotionTaxonomyText(await response.text());
  renderAffectTree();
  const labels = state.affect.taxonomy.labels.filter((label) => label.enabled !== false);
  const families = [...new Set(labels.map((label) => label.family))].sort();
  elements.affectFilterFamily.innerHTML =
    '<option value="">全部家族</option>' +
    families.map((family) => `<option value="${escapeHtml(family)}">${escapeHtml(family)}</option>`).join("");
  elements.affectFilterEmotion.innerHTML =
    '<option value="">全部標籤</option>' +
    labels.map((label) => `<option value="${escapeHtml(label.id)}">${escapeHtml(label.nameZhTw)} · ${escapeHtml(label.id)}</option>`).join("");
  return state.affect.taxonomy;
}

function renderAffectTree() {
  const taxonomy = state.affect.taxonomy;
  if (!taxonomy) return;
  const families = new Map();
  for (const label of taxonomy.labels.filter((item) => item.enabled !== false)) {
    if (!families.has(label.family)) families.set(label.family, []);
    families.get(label.family).push(label);
  }
  elements.affectTree.innerHTML = [...families]
    .map(([family, labels]) => `
      <div class="taxonomy-family">
        <strong>${escapeHtml(family)}</strong>
        <div class="taxonomy-family__labels">
          ${labels.map((label) => `
            <label title="${escapeHtml(label.description)}">
              <input type="checkbox" name="affect-emotion" value="${escapeHtml(label.id)}" />
              <span>${escapeHtml(label.nameZhTw)}</span>
            </label>
          `).join("")}
        </div>
      </div>
    `).join("");
}

function filteredAffectEntries() {
  const entries = state.affect.data?.entries ?? [];
  const filter = state.affect.filters;
  const labels = new Map((state.affect.taxonomy?.labels ?? []).map((label) => [label.id, label]));
  const query = filter.query.trim().toLocaleLowerCase();
  return entries
    .map((entry, index) => ({ entry, index }))
    .filter(({ entry }) => {
      const emotions = entry.emotions ?? [];
      if (query && !`${entry.word} ${entry.notes ?? ""} ${entry.source ?? ""}`.toLocaleLowerCase().includes(query)) return false;
      if (filter.family && !emotions.some((id) => labels.get(id)?.family === filter.family)) return false;
      if (filter.emotion && !emotions.includes(filter.emotion)) return false;
      if (filter.polarity && derivePolarity(emotions, state.affect.taxonomy, entry.polarity, entry.contextDependent) !== filter.polarity) return false;
      if (filter.status === "unannotated" && emotions.length) return false;
      if (filter.status === "pending" && !entry.migrationPending) return false;
      return true;
    });
}

function renderAffectEntries(selectIndex = state.affect.editingIndex ?? 0) {
  const entries = state.affect.data?.entries ?? [];
  const visible = filteredAffectEntries();
  elements.affectEntrySelect.innerHTML = visible.length
    ? visible.map(({ entry, index }) =>
        `<option value="${index}">${escapeHtml(entry.word)} · ${escapeHtml((entry.emotions ?? []).join(", ") || "無情緒標籤")}</option>`
      ).join("")
    : '<option value="">沒有符合條件的詞條</option>';
  elements.affectSummary.textContent =
    `${numberFormat.format(visible.length)} / ${numberFormat.format(entries.length)} 筆情感詞`;
  const chosen = visible.find(({ index }) => index === selectIndex) ?? visible[0];
  if (chosen) loadAffectEntry(chosen.index);
  else clearAffectEntry();
}

function updateAffectPolarityResult() {
  if (!state.affect.taxonomy) return;
  const emotions = $$('input[name="affect-emotion"]:checked', elements.affectTree)
    .map((input) => input.value);
  elements.affectPolarityResult.textContent = derivePolarity(
    emotions,
    state.affect.taxonomy,
    elements.affectPolarity.value || null,
    elements.affectContext.checked
  );
}

function clearAffectEntry() {
  state.affect.editingIndex = null;
  elements.affectWord.value = "";
  elements.affectSource.value = "manual";
  elements.affectContext.checked = false;
  elements.affectPolarity.value = "";
  elements.affectNotes.value = "";
  $$('input[name="affect-emotion"]', elements.affectTree).forEach((input) => {
    input.checked = false;
  });
  updateAffectPolarityResult();
}

function loadAffectEntry(index) {
  const entry = state.affect.data?.entries?.[index];
  if (!entry) return clearAffectEntry();
  state.affect.editingIndex = index;
  elements.affectEntrySelect.value = String(index);
  elements.affectWord.value = entry.word;
  elements.affectSource.value = entry.source ?? "";
  elements.affectContext.checked = Boolean(entry.contextDependent);
  elements.affectPolarity.value = entry.polarity ?? "";
  elements.affectNotes.value = entry.notes ?? "";
  const selected = new Set(entry.emotions ?? []);
  $$('input[name="affect-emotion"]', elements.affectTree).forEach((input) => {
    input.checked = selected.has(input.value);
  });
  updateAffectPolarityResult();
}

function applyAffectEntry() {
  const word = elements.affectWord.value.trim();
  if (!word) return showAlert("無法套用情感標註", "詞語不可為空。");
  const emotions = $$('input[name="affect-emotion"]:checked', elements.affectTree)
    .map((input) => input.value);
  const current =
    state.affect.editingIndex == null
      ? {}
      : state.affect.data.entries[state.affect.editingIndex];
  const entry = {
    ...current,
    word,
    emotions,
    contextDependent: elements.affectContext.checked,
    ...(elements.affectPolarity.value ? { polarity: elements.affectPolarity.value } : {}),
    ...(elements.affectSource.value.trim() ? { source: elements.affectSource.value.trim() } : {}),
    ...(elements.affectNotes.value.trim() ? { notes: elements.affectNotes.value.trim() } : {})
  };
  if (!elements.affectPolarity.value) delete entry.polarity;
  if (!elements.affectSource.value.trim()) delete entry.source;
  if (!elements.affectNotes.value.trim()) delete entry.notes;
  if (state.affect.editingIndex == null) {
    state.affect.data.entries.push(entry);
    state.affect.editingIndex = state.affect.data.entries.length - 1;
  } else {
    state.affect.data.entries[state.affect.editingIndex] = entry;
  }
  parseAffectLexiconText(JSON.stringify(state.affect.data), state.affect.taxonomy);
  markDirty("affect");
  renderAffectEntries(state.affect.editingIndex);
  toast("情感標註已套用", word);
}

async function loadAffectFile(file, handle = null) {
  showLoading("正在解析情感詞典", file.name);
  try {
    const taxonomy = await ensureAffectTaxonomy();
    const data = parseAffectLexiconText(await file.text(), taxonomy);
    Object.assign(state.affect, {
      data,
      fileHandle: handle,
      fileName: file.name,
      dirty: false,
      editingIndex: data.entries.length ? 0 : null
    });
    renderAffectEntries();
    toast("情感詞典已載入", `${data.entries.length} 筆詞條 · taxonomy ${taxonomy.version}`);
  } finally {
    hideLoading();
  }
}

async function loadTrainingFile(file, handle = null) {
  showLoading("正在載入訓練資料", `${file.name} · 正在建立資料列索引`);
  hideAlert();
  await nextPaint();
  try {
    const training = state.training;
    training.lines = parseTrainingText(await file.text());
    training.fileHandle = handle;
    training.fileName = file.name;
    training.dirty = false;
    training.startIndex = 0;
    training.selectedIndex = null;
    training.newWords.clear();
    elements.trainingQuery.value = "";
    showTrainingRange(0);
    renderNewWords();
    toast("訓練資料已載入", `${numberFormat.format(training.lines.length)} 筆資料可供抽查`);
  } finally {
    hideLoading();
  }
}

async function chooseSaveHandle(suggestedName, extension, mimeType) {
  if (!("showSaveFilePicker" in window) || !window.isSecureContext) return null;
  try {
    return await window.showSaveFilePicker({
      suggestedName,
      types: [
        {
          description: extension === ".json" ? "LingXi JSON 詞典" : "UTF-8 訓練資料",
          accept: { [mimeType]: [extension] }
        }
      ]
    });
  } catch (error) {
    if (error.name === "AbortError") return undefined;
    throw error;
  }
}

function downloadText(text, fileName, mimeType) {
  const blob = new Blob([text], { type: `${mimeType};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function saveCurrentFile() {
  const store = currentStore();
  if (!hasLoadedFile() || !store.dirty) return;
  hideAlert();

  try {
    let handle = store.fileHandle;
    if (!handle) {
      const extension = state.mode === "training" ? ".txt" : ".json";
      const mime = state.mode === "training" ? "text/plain" : "application/json";
      handle = await chooseSaveHandle(store.fileName || `lingxi${extension}`, extension, mime);
      if (handle === undefined) return;
    }

    showLoading(
      state.mode === "training" ? "正在儲存訓練資料" : "正在儲存 JSON 詞典",
      handle ? "正在安全寫入檔案" : "正在準備下載檔案"
    );
    await nextPaint();
    const text =
      state.mode === "dictionary"
        ? serializeDictionaryRows(state.dictionary.rows)
        : state.mode === "custom"
          ? serializeCustomLexicon(readCustomForm())
          : state.mode === "affect"
            ? serializeAffectLexicon(state.affect.data, state.affect.taxonomy)
            : state.training.lines.join("\r\n");
    const mime = state.mode === "training" ? "text/plain" : "application/json";

    if (handle) {
      const writable = await handle.createWritable();
      await writable.write(text);
      await writable.close();
      store.fileHandle = handle;
      store.fileName = handle.name || store.fileName;
    } else {
      downloadText(text, store.fileName || "lingxi-export.txt", mime);
    }
    store.dirty = false;
    toast("儲存完成", store.fileName);
  } catch (error) {
    showAlert("儲存失敗", `${error.message}。原始資料仍保留在畫面中，可再次儲存或下載。`);
  } finally {
    hideLoading();
  }
}

function dictionaryFilters() {
  return {
    query: elements.dictionaryQuery.value,
    tag: elements.filterTag.value,
    entity: elements.filterEntity.value,
    emotion: elements.filterEmotion.value,
    minFrequency: elements.filterMin.value,
    maxFrequency: elements.filterMax.value
  };
}

function activeFilterCount() {
  return Object.values(dictionaryFilters()).filter((value) => String(value).trim() !== "").length;
}

function applyDictionaryFilters() {
  const dict = state.dictionary;
  dict.filteredRows = filterAndSortRows(dict.rows, dictionaryFilters(), dict.sort);
  const pages = Math.max(1, Math.ceil(dict.filteredRows.length / dict.pageSize));
  dict.page = Math.min(Math.max(1, dict.page), pages);
  const count = activeFilterCount();
  elements.filterCount.textContent = String(count);
  elements.filterCount.hidden = count === 0;
  renderDictionary();
}

const debouncedDictionaryFilter = debounce(() => {
  state.dictionary.page = 1;
  applyDictionaryFilters();
});

function currentPageRows() {
  const dict = state.dictionary;
  const start = (dict.page - 1) * dict.pageSize;
  return dict.filteredRows.slice(start, start + dict.pageSize);
}

function renderDictionary() {
  const dict = state.dictionary;
  const pageRows = currentPageRows();
  elements.dictionaryBody.innerHTML = pageRows
    .map((row) => {
      const selected = dict.selected.has(row.id);
      return `
        <tr data-id="${row.id}" class="${selected ? "is-selected" : ""}">
          <td class="cell-check">
            <input class="row-select" type="checkbox" aria-label="選取 ${escapeHtml(
              row.word
            )}" ${selected ? "checked" : ""}>
          </td>
          <td>
            <button class="word-button" type="button" data-edit-id="${row.id}" title="${escapeHtml(
              row.word
            )}">${escapeHtml(row.word)}</button>
          </td>
          <td>
            <select data-field="tag" aria-label="${escapeHtml(row.word)}的詞性">
              ${optionMarkup(TAG_OPTIONS, row.tag)}
            </select>
          </td>
          <td class="column-entity">
            <select data-field="entity" aria-label="${escapeHtml(row.word)}的實體標註">
              ${optionMarkup(ENTITY_OPTIONS, row.entity)}
            </select>
          </td>
          <td class="column-emotion">
            <span class="legacy-emotion-chip" title="舊代碼僅供遷移預覽">
              ${escapeHtml(row.emotion === "None" ? "未標註" : row.emotion)}
            </span>
          </td>
          <td class="frequency-cell">
            <output class="frequency-value" aria-label="${escapeHtml(row.word)}的詞頻"
              title="詞頻由系統批次計算，不提供人工修改">${numberFormat.format(
                row.frequency
              )}</output>
          </td>
          <td class="cell-actions">
            <button class="icon-button row-edit-button" type="button" data-edit-id="${
              row.id
            }" aria-label="編輯 ${escapeHtml(row.word)}">
              <svg aria-hidden="true" viewBox="0 0 24 24">
                <path d="m5 16.5-.5 3 3-.5L18 8.5 15.5 6zM13.8 7.7l2.5 2.5"/>
              </svg>
            </button>
          </td>
        </tr>`;
    })
    .join("");

  const totalPages = Math.max(1, Math.ceil(dict.filteredRows.length / dict.pageSize));
  const start = dict.filteredRows.length ? (dict.page - 1) * dict.pageSize + 1 : 0;
  const end = Math.min(dict.page * dict.pageSize, dict.filteredRows.length);
  elements.resultSummary.textContent = `${numberFormat.format(start)}–${numberFormat.format(
    end
  )} / ${numberFormat.format(dict.filteredRows.length)} 筆${
    dict.filteredRows.length !== dict.rows.length
      ? `（全部 ${numberFormat.format(dict.rows.length)}）`
      : ""
  }`;
  elements.pageIndicator.textContent = `第 ${dict.page} / ${totalPages} 頁`;
  elements.firstPage.disabled = dict.page <= 1;
  elements.previousPage.disabled = dict.page <= 1;
  elements.nextPage.disabled = dict.page >= totalPages;
  elements.lastPage.disabled = dict.page >= totalPages;
  elements.tableEmpty.hidden = dict.filteredRows.length !== 0;

  const selectedOnPage = pageRows.filter((row) => dict.selected.has(row.id)).length;
  elements.selectPage.checked = pageRows.length > 0 && selectedOnPage === pageRows.length;
  elements.selectPage.indeterminate = selectedOnPage > 0 && selectedOnPage < pageRows.length;
  renderSelectionBar();
  updateSortButtons();
}

function updateSortButtons() {
  $$(".sort-button").forEach((button) => {
    const active = button.dataset.sort === state.dictionary.sort.key;
    button.classList.toggle("is-active", active);
    const marker = $("span", button);
    marker.textContent = active
      ? state.dictionary.sort.direction === "asc"
        ? "↑"
        : "↓"
      : "↕";
  });
}

function renderSelectionBar() {
  const count = state.dictionary.selected.size;
  elements.selectionCount.textContent = numberFormat.format(count);
  elements.selectionBar.hidden = state.mode !== "dictionary" || count === 0;
}

function resetDictionaryFilters(render = true) {
  elements.dictionaryQuery.value = "";
  elements.filterTag.value = "";
  elements.filterEntity.value = "";
  elements.filterEmotion.value = "";
  elements.filterMin.value = "";
  elements.filterMax.value = "";
  state.dictionary.page = 1;
  if (render && hasLoadedFile("dictionary")) applyDictionaryFilters();
}

function setDictionaryPage(page) {
  const totalPages = Math.max(
    1,
    Math.ceil(state.dictionary.filteredRows.length / state.dictionary.pageSize)
  );
  state.dictionary.page = Math.min(Math.max(1, page), totalPages);
  renderDictionary();
  $(".table-scroll")?.scrollTo({ top: 0, behavior: "smooth" });
}

function updateDictionaryRowFromControl(control) {
  const rowElement = control.closest("tr");
  const row = state.dictionary.rowById.get(Number(rowElement?.dataset.id));
  if (!row) return;
  const field = control.dataset.field;
  const value = control.value;
  if (row[field] === value) return;
  row[field] = value;
  control.value = String(value);
  markDirty("dictionary");
}

function openEntryDialog(row = null) {
  state.dictionary.editingId = row?.id ?? null;
  elements.entryTitle.textContent = row ? "編輯詞條" : "新增詞條";
  elements.entrySubmit.textContent = row ? "儲存詞條" : "新增詞條";
  elements.entryWord.value = row?.word ?? "";

  elements.entryTag.innerHTML = optionMarkup(TAG_OPTIONS, row?.tag ?? "unknownnew");
  elements.entryEntity.innerHTML = optionMarkup(ENTITY_OPTIONS, row?.entity ?? "None");
  elements.entryEmotion.innerHTML = optionMarkup(EMOTION_OPTIONS, row?.emotion ?? "None");
  elements.entryError.hidden = true;
  elements.entryDialog.showModal();
  window.setTimeout(() => elements.entryWord.focus(), 0);
}

function saveEntryFromDialog(event) {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  const dict = state.dictionary;
  const editingId = dict.editingId;
  const existing = editingId ? dict.rowById.get(editingId) : null;
  const word = elements.entryWord.value.trim();
  const duplicate = dict.rows.find((row) => row.word === word && row.id !== editingId);
  if (!word || duplicate) {
    elements.entryError.textContent = !word
      ? "詞語不可為空白。"
      : `「${word}」已存在，請直接編輯既有詞條。`;
    elements.entryError.hidden = false;
    elements.entryWord.focus();
    return;
  }

  const values = {
    word,
    tag: elements.entryTag.value,
    entity: elements.entryEntity.value,
    emotion: elements.entryEmotion.value
  };

  if (existing) {
    Object.assign(existing, values);
    toast("詞條已更新", word);
  } else {
    const row = {
      id: dict.nextId++,
      ...values,
      frequency: DEFAULT_NEW_ENTRY_FREQUENCY
    };
    dict.rows.unshift(row);
    dict.rowById.set(row.id, row);
    toast("詞條已新增", word);
  }

  markDirty("dictionary");
  dict.page = 1;
  applyDictionaryFilters();
  elements.entryDialog.close();
}

function clearSelection() {
  state.dictionary.selected.clear();
  renderDictionary();
}

async function markSelectedUnknown() {
  const dict = state.dictionary;
  if (!dict.selected.size) return;
  for (const id of dict.selected) {
    const row = dict.rowById.get(id);
    if (row) row.tag = "unknown";
  }
  const count = dict.selected.size;
  markDirty("dictionary");
  applyDictionaryFilters();
  toast("詞性已更新", `${numberFormat.format(count)} 筆標記為 unknown`);
}

function askConfirmation({ title, message, confirmLabel = "確認" }) {
  elements.confirmTitle.textContent = title;
  elements.confirmMessage.textContent = message;
  elements.confirmSubmit.textContent = confirmLabel;
  elements.confirmDialog.showModal();
  return new Promise((resolve) => {
    elements.confirmDialog.addEventListener(
      "close",
      () => resolve(elements.confirmDialog.returnValue === "confirm"),
      { once: true }
    );
  });
}

async function deleteSelectedRows() {
  const dict = state.dictionary;
  const count = dict.selected.size;
  if (!count) return;
  const confirmed = await askConfirmation({
    title: `刪除 ${numberFormat.format(count)} 筆詞條？`,
    message:
      count > 20
        ? "這次刪除筆數較多。刪除後仍需按「儲存變更」才會寫入檔案。"
        : "刪除後仍需按「儲存變更」才會寫入檔案。",
    confirmLabel: `刪除 ${numberFormat.format(count)} 筆`
  });
  if (!confirmed) return;
  dict.rows = dict.rows.filter((row) => !dict.selected.has(row.id));
  dict.rowById = new Map(dict.rows.map((row) => [row.id, row]));
  dict.selected.clear();
  markDirty("dictionary");
  applyDictionaryFilters();
  toast("詞條已刪除", `${numberFormat.format(count)} 筆待儲存`);
}

function openReplaceDialog(mode) {
  state.replaceMode = mode;
  state.replacePreview = null;
  elements.replaceTitle.textContent =
    mode === "dictionary" ? "批次代換詞語" : "代換訓練資料";
  elements.collectNewWordField.hidden = mode !== "training";
  elements.replaceBefore.value = "";
  elements.replaceAfter.value = "";
  elements.replacePreview.classList.remove("has-warning");
  elements.replacePreview.textContent = "輸入尋找內容後，這裡會顯示影響範圍。";
  elements.replaceSubmit.disabled = true;
  elements.replaceDialog.showModal();
  window.setTimeout(() => elements.replaceBefore.focus(), 0);
}

function updateReplacePreview() {
  const before = elements.replaceBefore.value;
  const after = elements.replaceAfter.value;
  elements.replacePreview.classList.remove("has-warning");
  if (!before) {
    state.replacePreview = null;
    elements.replacePreview.textContent = "輸入尋找內容後，這裡會顯示影響範圍。";
    elements.replaceSubmit.disabled = true;
    return;
  }

  try {
    if (state.replaceMode === "dictionary") {
      const preview = previewDictionaryReplacement(state.dictionary.rows, before, after);
      state.replacePreview = preview;
      elements.replacePreview.innerHTML = `
        <span>
          找到 <strong>${numberFormat.format(preview.matchedCount)}</strong> 筆，
          可安全修改 <strong>${numberFormat.format(preview.changes.length)}</strong> 筆。
          ${
            preview.conflicts.length
              ? `另有 <strong>${numberFormat.format(
                  preview.conflicts.length
                )}</strong> 筆因重複或空白而跳過。`
              : "沒有詞條衝突。"
          }
        </span>`;
      elements.replacePreview.classList.toggle("has-warning", preview.conflicts.length > 0);
      elements.replaceSubmit.disabled = preview.changes.length === 0;
    } else {
      const result = replaceTrainingLines(state.training.lines, before, after);
      state.replacePreview = result;
      const potential = inferPotentialWord(after);
      elements.replacePreview.innerHTML = `
        <span>
          將修改 <strong>${numberFormat.format(result.changedCount)}</strong> 筆資料。
          ${
            potential
              ? `潛在新詞：<strong>${escapeHtml(potential)}</strong>。`
              : "目前代換結果不會產生兩字以上的新詞候選。"
          }
        </span>`;
      elements.replaceSubmit.disabled = result.changedCount === 0;
    }
  } catch (error) {
    state.replacePreview = null;
    elements.replacePreview.textContent = error.message;
    elements.replacePreview.classList.add("has-warning");
    elements.replaceSubmit.disabled = true;
  }
}

const debouncedReplacePreview = debounce(updateReplacePreview, 180);

function applyReplacementFromDialog(event) {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  if (!state.replacePreview) return;

  if (state.replaceMode === "dictionary") {
    const changed = applyDictionaryReplacement(state.replacePreview);
    state.dictionary.rowById = new Map(state.dictionary.rows.map((row) => [row.id, row]));
    markDirty("dictionary");
    applyDictionaryFilters();
    toast("批次代換完成", `${numberFormat.format(changed)} 筆詞條待儲存`);
  } else {
    const result = state.replacePreview;
    state.training.lines = result.lines;
    if (elements.collectNewWord.checked) {
      const potential = inferPotentialWord(elements.replaceAfter.value);
      if (potential) state.training.newWords.add(potential);
    }
    markDirty("training");
    showTrainingRange(state.training.startIndex);
    renderNewWords();
    toast("訓練資料已代換", `${numberFormat.format(result.changedCount)} 筆資料待儲存`);
  }
  elements.replaceDialog.close();
}

function showTrainingRange(startIndex) {
  const training = state.training;
  const maxStart = Math.max(0, training.lines.length - 1);
  training.startIndex = Math.min(Math.max(0, Math.trunc(startIndex) || 0), maxStart);
  training.viewItems = training.lines
    .slice(training.startIndex, training.startIndex + 100)
    .map((line, offset) => ({ index: training.startIndex + offset, line }));
  training.viewLabel = `第 ${numberFormat.format(training.startIndex + 1)}–${numberFormat.format(
    Math.min(training.startIndex + 100, training.lines.length)
  )} 筆`;
  training.selectedIndex = null;
  elements.trainingStartIndex.value = String(training.startIndex + 1);
  renderTraining();
}

function searchTraining() {
  const training = state.training;
  const query = elements.trainingQuery.value;
  if (!query) {
    showTrainingRange(training.startIndex);
    return;
  }
  const items = [];
  for (let index = 0; index < training.lines.length && items.length < 100; index += 1) {
    if (training.lines[index].includes(query)) {
      items.push({ index, line: training.lines[index] });
    }
  }
  training.viewItems = items;
  training.viewLabel = `「${query}」的前 ${numberFormat.format(items.length)} 筆結果`;
  training.selectedIndex = null;
  renderTraining();
}

const debouncedTrainingSearch = debounce(searchTraining);

function renderTraining() {
  const training = state.training;
  elements.corpusViewer.innerHTML = training.viewItems.length
    ? training.viewItems
        .map(
          ({ index, line }) => `
            <button class="corpus-line${
              training.selectedIndex === index ? " is-selected" : ""
            }" type="button" role="listitem" data-line-index="${index}">
              <span class="corpus-line__number">${numberFormat.format(index + 1)}</span>
              <span class="corpus-line__text">${escapeHtml(line || "（空白行）")}</span>
            </button>`
        )
        .join("")
    : `<div class="table-empty"><strong>找不到符合內容</strong><p>清除查詢文字或改用其他片段。</p></div>`;
  elements.trainingViewLabel.textContent = training.viewLabel;
  elements.trainingSummary.textContent = `${numberFormat.format(
    training.lines.length
  )} 筆資料 · 目前顯示 ${numberFormat.format(training.viewItems.length)} 筆`;
  elements.deleteTrainingLine.disabled = training.selectedIndex == null;
  updateChrome();
}

function randomTrainingRange() {
  const training = state.training;
  if (!training.lines.length) return;
  elements.trainingQuery.value = "";
  const maxStart = Math.max(0, training.lines.length - 100);
  showTrainingRange(Math.floor(Math.random() * (maxStart + 1)));
}

async function deleteSelectedTrainingLine() {
  const training = state.training;
  if (training.selectedIndex == null) return;
  const index = training.selectedIndex;
  const preview = training.lines[index]?.slice(0, 80) || "空白行";
  const confirmed = await askConfirmation({
    title: `刪除第 ${numberFormat.format(index + 1)} 筆資料？`,
    message: preview.length >= 80 ? `${preview}…` : preview,
    confirmLabel: "刪除這筆資料"
  });
  if (!confirmed) return;
  training.lines.splice(index, 1);
  training.selectedIndex = null;
  markDirty("training");
  showTrainingRange(Math.min(training.startIndex, Math.max(0, training.lines.length - 1)));
  toast("訓練資料已刪除", "變更尚未寫入檔案");
}

function renderNewWords() {
  const words = [...state.training.newWords].sort((a, b) => a.localeCompare(b, "zh-Hant"));
  elements.newWordCount.textContent = numberFormat.format(words.length);
  elements.openNewWords.disabled = words.length === 0;
  elements.newWordList.innerHTML = words.length
    ? words.map((word) => `<li>${escapeHtml(word)}</li>`).join("")
    : "<li>目前沒有潛在新詞。</li>";
}

function openNewWordDrawer() {
  renderNewWords();
  elements.newWordDrawer.hidden = false;
  elements.drawerBackdrop.hidden = false;
  $("#close-new-word-drawer").focus();
}

function closeNewWordDrawer() {
  elements.newWordDrawer.hidden = true;
  elements.drawerBackdrop.hidden = true;
}

function loadDictionaryDemo() {
  const demo = {
    台積電: ["nt", 100000],
    台北市: ["ns", 82000],
    金管會: ["nt", 48000],
    開心: ["a", 1200, "Happy"],
    王小明: ["nr", 30, "ChName"],
    鄉民: ["n", 780, "Catchword"],
    未確認新詞: ["unknownnew", 0]
  };
  const parsed = parseDictionaryText(JSON.stringify(demo));
  Object.assign(state.dictionary, {
    rows: parsed.rows,
    filteredRows: [],
    rowById: new Map(parsed.rows.map((row) => [row.id, row])),
    fileHandle: null,
    fileName: "Dict.demo.json",
    dirty: true,
    warnings: [],
    page: 1,
    nextId: parsed.rows.length + 1
  });
  state.dictionary.selected.clear();
  resetDictionaryFilters(false);
  applyDictionaryFilters();
  updateChrome();
  toast("示範詞典已載入", "可直接編輯並另存為 JSON");
}

function loadTrainingDemo() {
  const lines = [
    "台北|股市|今日|開低|走低",
    "護國|神山|台|積|電|收盤|上漲",
    "市場|把|台|積|電|的|法說會|當成|利多",
    "一個|完整|詞條|不應|被|拆開",
    "我們|正在|校訂|繁體中文|分詞|資料",
    "這是|可供|查詢|與|代換|的|示範|資料"
  ];
  Object.assign(state.training, {
    lines,
    fileHandle: null,
    fileName: "training.demo.txt",
    dirty: true,
    startIndex: 0,
    selectedIndex: null
  });
  state.training.newWords.clear();
  elements.trainingQuery.value = "";
  showTrainingRange(0);
  renderNewWords();
  updateChrome();
  toast("示範資料已載入", "可嘗試將「台|積|電」代換為「台積電」");
}


function createCustomLexicon() {
  Object.assign(state.custom, {
    data: { schemaVersion: 1, id: "new-domain", domain: "general", priority: 0, enabled: true, entries: [] },
    fileHandle: null,
    fileName: "custom-lexicon.json",
    dirty: true
  });
  renderCustomForm();
  updateChrome();
}

async function createAffectLexicon() {
  const taxonomy = await ensureAffectTaxonomy();
  Object.assign(state.affect, {
    data: { schemaVersion: 1, taxonomyVersion: taxonomy.version, entries: [] },
    fileHandle: null,
    fileName: "emotion-lexicon.json",
    dirty: true,
    editingIndex: null
  });
  renderAffectEntries();
  updateChrome();
}

async function previewEmotionMigration() {
  if (!state.dictionary.rows.length) return;
  const taxonomy = await ensureAffectTaxonomy();
  const preview = previewLegacyEmotionMigration(state.dictionary.rows);
  const lexicon = {
    schemaVersion: 1,
    taxonomyVersion: taxonomy.version,
    entries: preview.entries
  };
  downloadText(
    JSON.stringify(lexicon, null, 2) + "\n",
    "emotion-lexicon.migration-preview.json",
    "application/json"
  );
  if (preview.pending.length) {
    downloadText(
      JSON.stringify({ schemaVersion: 1, pending: preview.pending }, null, 2) + "\n",
      "emotion-migration.pending.json",
      "application/json"
    );
  }
  toast(
    "遷移預覽已下載",
    `${preview.entries.length} 筆可轉換 · ${preview.pending.length} 筆待人工確認；原檔未變更`
  );
}

function deleteCurrentAffectEntry() {
  const index = state.affect.editingIndex;
  if (index == null) return;
  const [removed] = state.affect.data.entries.splice(index, 1);
  state.affect.editingIndex = null;
  markDirty("affect");
  renderAffectEntries();
  toast("情感詞條已刪除", removed.word);
}

function updateAffectFilters() {
  Object.assign(state.affect.filters, {
    query: elements.affectFilterQuery.value,
    family: elements.affectFilterFamily.value,
    emotion: elements.affectFilterEmotion.value,
    polarity: elements.affectFilterPolarity.value,
    status: elements.affectFilterStatus.value
  });
  renderAffectEntries();
}

function installEventHandlers() {
  $$(".mode-switch__button").forEach((button) =>
    button.addEventListener("click", () => setMode(button.dataset.mode))
  );
  elements.openButton.addEventListener("click", openCurrentFile);
  elements.saveButton.addEventListener("click", saveCurrentFile);
  $("#dismiss-alert").addEventListener("click", hideAlert);
  $$('[data-action="open-dictionary"]').forEach((button) =>
    button.addEventListener("click", async () => {
      setMode("dictionary");
      await openCurrentFile();
    })
  );
  $$('[data-action="open-training"]').forEach((button) =>
    button.addEventListener("click", async () => {
      setMode("training");
      await openCurrentFile();
    })
  );
  $$('[data-action="open-custom"]').forEach((button) =>
    button.addEventListener("click", async () => {
      setMode("custom");
      await openCurrentFile();
    })
  );
  $$('[data-action="open-affect"]').forEach((button) =>
    button.addEventListener("click", async () => {
      setMode("affect");
      await openCurrentFile();
    })
  );
  $$('[data-action="new-custom"]').forEach((button) =>
    button.addEventListener("click", () => {
      setMode("custom");
      createCustomLexicon();
    })
  );
  $$('[data-action="new-affect"]').forEach((button) =>
    button.addEventListener("click", async () => {
      setMode("affect");
      await createAffectLexicon();
    })
  );
  $$('[data-action="clear-filters"]').forEach((button) =>
    button.addEventListener("click", () => resetDictionaryFilters())
  );
  $("#load-dictionary-demo").addEventListener("click", loadDictionaryDemo);
  $("#load-training-demo").addEventListener("click", loadTrainingDemo);

  $("#preview-emotion-migration").addEventListener("click", previewEmotionMigration);
  [elements.customId, elements.customDomain, elements.customPriority, elements.customEntries].forEach(
    (control) => control.addEventListener("input", () => {
      if (!state.custom.data) return;
      markDirty("custom");
      const count = elements.customEntries.value.split(/\r?\n/).filter((line) => line.trim()).length;
      elements.customSummary.textContent = `${numberFormat.format(count)} 筆詞條 · 不含詞頻`;
    })
  );
  elements.customEnabled.addEventListener("change", () => {
    if (state.custom.data) markDirty("custom");
  });
  elements.affectEntrySelect.addEventListener("change", () => {
    if (elements.affectEntrySelect.value !== "") loadAffectEntry(Number(elements.affectEntrySelect.value));
  });
  $("#affect-new").addEventListener("click", clearAffectEntry);
  $("#affect-delete").addEventListener("click", deleteCurrentAffectEntry);
  $("#affect-apply").addEventListener("click", applyAffectEntry);
  elements.affectTree.addEventListener("change", updateAffectPolarityResult);
  elements.affectPolarity.addEventListener("change", updateAffectPolarityResult);
  elements.affectContext.addEventListener("change", updateAffectPolarityResult);
  [
    elements.affectFilterQuery,
    elements.affectFilterFamily,
    elements.affectFilterEmotion,
    elements.affectFilterPolarity,
    elements.affectFilterStatus
  ].forEach((control) =>
    control.addEventListener(control.tagName === "INPUT" ? "input" : "change", updateAffectFilters)
  );

  $("#toggle-filter-button").addEventListener("click", (event) => {
    const open = elements.filterPanel.hidden;
    elements.filterPanel.hidden = !open;
    event.currentTarget.setAttribute("aria-expanded", String(open));
  });
  $("#clear-filter-button").addEventListener("click", () => resetDictionaryFilters());
  elements.dictionaryQuery.addEventListener("input", debouncedDictionaryFilter);
  [elements.filterTag, elements.filterEntity, elements.filterEmotion].forEach((control) =>
    control.addEventListener("change", () => {
      state.dictionary.page = 1;
      applyDictionaryFilters();
    })
  );
  [elements.filterMin, elements.filterMax].forEach((control) =>
    control.addEventListener("input", debouncedDictionaryFilter)
  );

  elements.dictionaryBody.addEventListener("change", (event) => {
    const row = event.target.closest("tr");
    if (!row) return;
    const id = Number(row.dataset.id);
    if (event.target.classList.contains("row-select")) {
      if (event.target.checked) state.dictionary.selected.add(id);
      else state.dictionary.selected.delete(id);
      row.classList.toggle("is-selected", event.target.checked);
      renderSelectionBar();
      const pageRows = currentPageRows();
      const selectedOnPage = pageRows.filter((item) => state.dictionary.selected.has(item.id)).length;
      elements.selectPage.checked = selectedOnPage === pageRows.length;
      elements.selectPage.indeterminate = selectedOnPage > 0 && selectedOnPage < pageRows.length;
      return;
    }
    if (event.target.dataset.field) updateDictionaryRowFromControl(event.target);
  });
  elements.dictionaryBody.addEventListener("click", (event) => {
    const button = event.target.closest("[data-edit-id]");
    if (!button) return;
    openEntryDialog(state.dictionary.rowById.get(Number(button.dataset.editId)));
  });
  elements.selectPage.addEventListener("change", () => {
    currentPageRows().forEach((row) => {
      if (elements.selectPage.checked) state.dictionary.selected.add(row.id);
      else state.dictionary.selected.delete(row.id);
    });
    renderDictionary();
  });
  $$(".sort-button").forEach((button) =>
    button.addEventListener("click", () => {
      const key = button.dataset.sort;
      const sort = state.dictionary.sort;
      if (sort.key === key) sort.direction = sort.direction === "asc" ? "desc" : "asc";
      else state.dictionary.sort = { key, direction: "asc" };
      state.dictionary.page = 1;
      applyDictionaryFilters();
    })
  );
  elements.pageSize.addEventListener("change", () => {
    state.dictionary.pageSize = Number(elements.pageSize.value);
    state.dictionary.page = 1;
    renderDictionary();
  });
  elements.firstPage.addEventListener("click", () => setDictionaryPage(1));
  elements.previousPage.addEventListener("click", () =>
    setDictionaryPage(state.dictionary.page - 1)
  );
  elements.nextPage.addEventListener("click", () => setDictionaryPage(state.dictionary.page + 1));
  elements.lastPage.addEventListener("click", () =>
    setDictionaryPage(
      Math.max(1, Math.ceil(state.dictionary.filteredRows.length / state.dictionary.pageSize))
    )
  );

  $("#add-entry-button").addEventListener("click", () => openEntryDialog());
  elements.entryForm.addEventListener("submit", saveEntryFromDialog);
  $("#clear-selection-button").addEventListener("click", clearSelection);
  $("#mark-unknown-button").addEventListener("click", markSelectedUnknown);
  $("#delete-selected-button").addEventListener("click", deleteSelectedRows);

  $("#open-replace-button").addEventListener("click", () => openReplaceDialog("dictionary"));
  $("#training-replace-button").addEventListener("click", () => openReplaceDialog("training"));
  [elements.replaceBefore, elements.replaceAfter].forEach((input) =>
    input.addEventListener("input", debouncedReplacePreview)
  );
  elements.replaceForm.addEventListener("submit", applyReplacementFromDialog);

  elements.trainingQuery.addEventListener("input", debouncedTrainingSearch);
  $("#random-training-button").addEventListener("click", randomTrainingRange);
  $("#jump-training-button").addEventListener("click", () => {
    elements.trainingQuery.value = "";
    showTrainingRange(Number(elements.trainingStartIndex.value) - 1);
  });
  elements.corpusViewer.addEventListener("click", (event) => {
    const line = event.target.closest("[data-line-index]");
    if (!line) return;
    state.training.selectedIndex = Number(line.dataset.lineIndex);
    renderTraining();
  });
  elements.deleteTrainingLine.addEventListener("click", deleteSelectedTrainingLine);
  elements.openNewWords.addEventListener("click", openNewWordDrawer);
  $("#close-new-word-drawer").addEventListener("click", closeNewWordDrawer);
  elements.drawerBackdrop.addEventListener("click", closeNewWordDrawer);
  $("#clear-new-words-button").addEventListener("click", async () => {
    const confirmed = await askConfirmation({
      title: "清空潛在新詞？",
      message: "這只會清除目前瀏覽器中的候選清單，不影響訓練資料。",
      confirmLabel: "清空清單"
    });
    if (!confirmed) return;
    state.training.newWords.clear();
    renderNewWords();
    closeNewWordDrawer();
  });
  $("#download-new-words-button").addEventListener("click", () => {
    const text = [...state.training.newWords].join("\r\n");
    const date = new Date().toISOString().slice(0, 10).replaceAll("-", "");
    downloadText(text, `潛在新詞列表_${date}.txt`, "text/plain");
  });

  window.addEventListener("keydown", (event) => {
    const modifier = event.ctrlKey || event.metaKey;
    if (modifier && event.key.toLocaleLowerCase() === "s") {
      event.preventDefault();
      saveCurrentFile();
    }
    if (
      event.key === "/" &&
      !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName) &&
      !$$("dialog[open]").length
    ) {
      event.preventDefault();
      (state.mode === "dictionary"
        ? elements.dictionaryQuery
        : elements.trainingQuery
      ).focus();
    }
    if (event.key === "Escape" && !elements.newWordDrawer.hidden) closeNewWordDrawer();
  });
  window.addEventListener("beforeunload", (event) => {
    if (![state.dictionary, state.custom, state.affect, state.training].some((store) => store.dirty)) return;
    event.preventDefault();
    event.returnValue = "";
  });
}

populateStaticOptions();
installEventHandlers();
updateChrome();
