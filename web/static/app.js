/* DouyinLiveRecorder web dashboard (Vue 3, no build step). */
const { createApp, reactive, ref, computed, nextTick, onMounted } = Vue;

const TOKEN_KEY = 'dlr_token';
const LEVEL_RANK = { debug: 0, info: 1, warning: 2, error: 3 };
const GROUP_ORDER = ['录制', '文件与后处理', '代理', '推送', '账号', 'Cookie', 'Web', '界面'];

createApp({
    setup() {
        const pages = [
            { key: 'rooms', label: '直播间' },
            { key: 'files', label: '录制文件' },
            { key: 'logs', label: '运行日志' },
            { key: 'settings', label: '设置' },
            { key: 'system', label: '环境与关于' },
        ];
        const stateFilters = [
            { key: 'all', label: '全部状态', states: null },
            { key: 'recording', label: '录制中', states: ['recording'] },
            { key: 'live', label: '直播中', states: ['recording', 'live'] },
            { key: 'waiting', label: '未开播', states: ['waiting', 'idle', 'checking'] },
            { key: 'paused', label: '已暂停', states: ['paused'] },
            { key: 'error', label: '出错', states: ['error'] },
        ];

        const page = ref('rooms');
        const token = ref(localStorage.getItem(TOKEN_KEY) || '');
        const auth = reactive({ required: false, authenticated: false, checked: false });
        const password = ref('');
        const loginError = ref('');
        const wsOnline = ref(false);
        const now = ref(Date.now() / 1000);

        const rooms = ref([]);
        const roomSearch = ref('');
        const roomFilter = ref('all');
        const files = ref([]);
        const fileSearch = ref('');
        const logs = ref([]);
        const logLevel = ref(1);
        const logFollow = ref(true);
        const logBox = ref(null);
        const system = ref({});
        const meta = reactive({ qualities: [], platforms: [], push_channels: [], specs: [], cookie_specs: [] });
        const settings = ref({ values: {}, cookies: {} });
        const form = reactive({ values: {}, cookies: {} });
        const settingTab = ref('录制');
        const cookieSearch = ref('');
        const toasts = ref([]);
        const video = ref(null);

        const roomDialog = reactive({ show: false, id: null, url: '', quality: '原画', name: '', enabled: true });
        const importDialog = reactive({ show: false, text: '', quality: '原画' });
        const preview = reactive({ show: false, name: '', url: '', player: null });

        const needLogin = computed(() => auth.checked && auth.required && !auth.authenticated);

        // ------------------------------------------------------------ helpers
        function toast(text, type = 'info') {
            const id = Math.random();
            toasts.value.push({ id, text, type });
            setTimeout(() => { toasts.value = toasts.value.filter(t => t.id !== id); }, 4000);
        }

        async function api(method, path, body) {
            const headers = { 'Content-Type': 'application/json' };
            if (token.value) headers.Authorization = 'Bearer ' + token.value;
            const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
            if (res.status === 401) {
                auth.authenticated = false;
                throw new Error('未登录或登录已过期');
            }
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || ('请求失败: ' + res.status));
            return data;
        }

        async function guarded(promise, okText) {
            try {
                const result = await promise;
                if (okText) toast(okText, 'ok');
                return result;
            } catch (e) {
                toast(e.message, 'error');
                return null;
            }
        }

        const pad = n => String(n).padStart(2, '0');
        function datetime(ts) {
            const d = new Date(ts * 1000);
            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
        }
        function duration(r) {
            if (!r.record_started_at) return '';
            const s = Math.max(0, Math.floor(now.value - r.record_started_at));
            return `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s % 3600 / 60))}:${pad(s % 60)}`;
        }
        function nextCheck(r) {
            if (!r.next_check || !r.enabled) return '';
            return Math.max(0, Math.floor(r.next_check - now.value)) + ' 秒后';
        }
        function size(bytes) {
            const units = ['B', 'KB', 'MB', 'GB', 'TB'];
            let i = 0, v = bytes;
            while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
            return i === 0 ? `${v} B` : `${v.toFixed(1)} ${units[i]}`;
        }
        const dirname = p => p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '';

        // ------------------------------------------------------------ auth
        async function checkAuth() {
            const headers = token.value ? { Authorization: 'Bearer ' + token.value } : {};
            const res = await fetch('/api/auth', { headers }).then(r => r.json());
            auth.required = res.required;
            auth.authenticated = res.authenticated;
            auth.checked = true;
            if (!auth.required || auth.authenticated) await boot();
        }
        async function login() {
            loginError.value = '';
            try {
                const res = await fetch('/api/login', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password: password.value }),
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || '登录失败');
                token.value = data.token;
                localStorage.setItem(TOKEN_KEY, data.token);
                password.value = '';
                auth.authenticated = true;
                await boot();
            } catch (e) {
                loginError.value = e.message;
            }
        }
        async function logout() {
            await api('POST', '/api/logout').catch(() => {});
            localStorage.removeItem(TOKEN_KEY);
            token.value = '';
            auth.authenticated = false;
            if (ws) ws.close();
        }

        // ------------------------------------------------------------ data loading
        let booted = false;
        async function boot() {
            Object.assign(meta, await api('GET', '/api/meta'));
            await Promise.all([loadRooms(), loadSettings(), loadSystem(), loadLogs()]);
            connectWs();
            if (!booted) {
                booted = true;
                setInterval(() => { now.value = Date.now() / 1000; }, 1000);
                setInterval(() => { if (page.value === 'system') loadSystem(); }, 10000);
            }
        }
        async function loadRooms() { rooms.value = await api('GET', '/api/rooms'); }
        async function loadFiles() { files.value = await guarded(api('GET', '/api/recordings')) || []; }
        async function loadSystem() { system.value = await guarded(api('GET', '/api/system')) || system.value; }
        async function loadLogs() { logs.value = await api('GET', '/api/logs?limit=1000'); scrollLogs(); }
        async function loadSettings() {
            settings.value = await api('GET', '/api/settings');
            resetForm();
        }
        function resetForm() {
            form.values = JSON.parse(JSON.stringify(settings.value.values));
            form.cookies = JSON.parse(JSON.stringify(settings.value.cookies));
        }

        // ------------------------------------------------------------ websocket
        let ws = null;
        let retry = 0;
        function connectWs() {
            if (ws && ws.readyState <= 1) return;
            const proto = location.protocol === 'https:' ? 'wss' : 'ws';
            ws = new WebSocket(`${proto}://${location.host}/ws/events?token=${encodeURIComponent(token.value)}`);
            ws.onopen = () => { wsOnline.value = true; retry = 0; loadRooms(); };
            ws.onmessage = msg => handleEvent(JSON.parse(msg.data));
            ws.onclose = ev => {
                wsOnline.value = false;
                if (ev.code === 4401) { auth.authenticated = false; return; }
                if (!auth.required || auth.authenticated) setTimeout(connectWs, Math.min(10000, 1000 * 2 ** retry++));
            };
        }
        function handleEvent(e) {
            switch (e.type) {
                case 'room_state': {
                    const i = rooms.value.findIndex(r => r.id === e.room.id);
                    if (i >= 0) rooms.value[i] = e.room;
                    break;
                }
                case 'room_added':
                case 'room_removed':
                    loadRooms();
                    break;
                case 'log':
                    logs.value.push(e);
                    if (logs.value.length > 3000) logs.value.splice(0, logs.value.length - 3000);
                    scrollLogs();
                    break;
                case 'recording_started':
                    toast(`开始录制：${e.room.name}`, 'ok');
                    break;
                case 'recording_finished':
                    if (!e.stopped) toast(e.return_code === 0 ? `录制完成：${e.room.name}` : `录制出错：${e.room.name}`, e.return_code === 0 ? 'ok' : 'error');
                    if (page.value === 'files') loadFiles();
                    break;
                case 'file_converted':
                    if (page.value === 'files') loadFiles();
                    break;
                case 'settings_changed':
                    if (!settingsDirty.value) loadSettings();
                    break;
                case 'disk_low':
                    toast(`磁盘剩余 ${e.free_gb.toFixed(1)} GB，低于阈值，已停止录制`, 'error');
                    break;
            }
        }
        function scrollLogs() {
            if (!logFollow.value) return;
            nextTick(() => { if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight; });
        }

        // ------------------------------------------------------------ rooms
        const monitoringCount = computed(() => rooms.value.filter(r => r.enabled).length);
        const recordingCount = computed(() => rooms.value.filter(r => r.state === 'recording').length);
        const filteredRooms = computed(() => {
            const filter = stateFilters.find(f => f.key === roomFilter.value);
            const q = roomSearch.value.trim().toLowerCase();
            return rooms.value
                .filter(r => !filter.states || filter.states.includes(r.state))
                .filter(r => !q || [r.name, r.platform, r.url, r.title].join(' ').toLowerCase().includes(q))
                .sort((a, b) => a.index - b.index);
        });

        function openRoomDialog(r) {
            Object.assign(roomDialog, r
                ? { show: true, id: r.id, url: r.url, quality: r.quality, name: r.name, enabled: r.enabled }
                : { show: true, id: null, url: '', quality: settings.value.values.quality || '原画', name: '', enabled: true });
        }
        async function saveRoom() {
            const body = { url: roomDialog.url, quality: roomDialog.quality, name: roomDialog.name, enabled: roomDialog.enabled };
            const ok = roomDialog.id
                ? await guarded(api('PATCH', `/api/rooms/${roomDialog.id}`, body), '已保存')
                : await guarded(api('POST', '/api/rooms', body), '已添加');
            if (ok) { roomDialog.show = false; loadRooms(); }
        }
        async function importRooms() {
            const res = await guarded(api('POST', '/api/rooms/import', { text: importDialog.text, quality: importDialog.quality }));
            if (!res) return;
            toast(`成功导入 ${res.added} 个` + (res.skipped.length ? `，跳过 ${res.skipped.length} 个` : ''), 'ok');
            if (res.skipped.length) alert('以下内容已跳过：\n' + res.skipped.join('\n'));
            importDialog.show = false;
            importDialog.text = '';
            loadRooms();
        }
        async function roomAction(r, action) {
            await guarded(api('POST', `/api/rooms/${r.id}/${action}`));
        }
        async function bulk(action) {
            if (action === 'pause_all' && !confirm('暂停全部直播间？正在录制的会停止并保存。')) return;
            await guarded(api('POST', `/api/bulk/${action}`));
        }
        async function deleteRoom(r) {
            const extra = r.state === 'recording' ? '\n该直播间正在录制，将停止录制并保存已录制的文件。' : '';
            if (!confirm(`确定删除「${r.name || r.url}」吗？${extra}`)) return;
            await guarded(api('DELETE', `/api/rooms/${r.id}`), '已删除');
        }

        // ------------------------------------------------------------ preview
        function openPreview(r) {
            Object.assign(preview, { show: true, name: r.name, url: r.stream_url });
            nextTick(() => {
                const el = video.value;
                const url = r.stream_url;
                if (/\.flv/i.test(url) && window.flvjs && flvjs.isSupported()) {
                    preview.player = flvjs.createPlayer({ type: 'flv', url, isLive: true });
                    preview.player.attachMediaElement(el);
                    preview.player.load();
                    preview.player.play();
                } else if (window.Hls && Hls.isSupported()) {
                    preview.player = new Hls();
                    preview.player.loadSource(url);
                    preview.player.attachMedia(el);
                } else {
                    el.src = url;
                }
            });
        }
        function closePreview() {
            if (preview.player) { preview.player.destroy(); preview.player = null; }
            preview.show = false;
        }

        // ------------------------------------------------------------ files
        const filteredFiles = computed(() => {
            const q = fileSearch.value.trim().toLowerCase();
            return files.value.filter(f => !q || f.relative.toLowerCase().includes(q));
        });
        const downloadUrl = f => `/api/recordings/download?path=${encodeURIComponent(f.relative)}&token=${encodeURIComponent(token.value)}`;
        async function convertFile(f) {
            await guarded(api('POST', '/api/recordings/convert', { path: f.relative }), '已开始转换，完成后自动刷新');
        }
        async function deleteFile(f) {
            if (!confirm(`确定永久删除 ${f.name} 吗？`)) return;
            if (await guarded(api('DELETE', `/api/recordings?path=${encodeURIComponent(f.relative)}`), '已删除')) loadFiles();
        }

        // ------------------------------------------------------------ logs
        const filteredLogs = computed(() => logs.value.filter(l => (LEVEL_RANK[l.level] ?? 1) >= logLevel.value));

        // ------------------------------------------------------------ settings
        const settingGroups = computed(() => {
            const present = new Set(meta.specs.map(s => s.group));
            return GROUP_ORDER.filter(g => g === 'Cookie' || present.has(g));
        });
        const specsOf = group => meta.specs.filter(s => s.group === group);
        const filteredCookieSpecs = computed(() => {
            const q = cookieSearch.value.trim().toLowerCase();
            return meta.cookie_specs.filter(c => !q || c.label.toLowerCase().includes(q) || c.key.includes(q));
        });
        const changedValues = computed(() => {
            const out = {};
            for (const [k, v] of Object.entries(form.values)) if (v !== settings.value.values[k]) out[k] = v;
            return out;
        });
        const changedCookies = computed(() => {
            const out = {};
            for (const [k, v] of Object.entries(form.cookies)) if ((v || '') !== (settings.value.cookies[k] || '')) out[k] = v || '';
            return out;
        });
        const settingsDirty = computed(() => Object.keys(changedValues.value).length + Object.keys(changedCookies.value).length > 0);

        const channelList = () => (form.values.push_channels || '').split(/[,，]/).map(s => s.trim()).filter(Boolean);
        const hasChannel = c => channelList().map(s => s.toUpperCase()).includes(c.toUpperCase());
        function toggleChannel(c, on) {
            const list = channelList().filter(s => s.toUpperCase() !== c.toUpperCase());
            if (on) list.push(c);
            form.values.push_channels = meta.push_channels.filter(x => list.map(s => s.toUpperCase()).includes(x.toUpperCase())).join(', ');
        }

        async function saveSettings() {
            const count = Object.keys(changedValues.value).length + Object.keys(changedCookies.value).length;
            if (!count) { toast('没有需要保存的修改'); return; }
            const res = await guarded(api('PATCH', '/api/settings', { values: changedValues.value, cookies: changedCookies.value }),
                `已保存 ${count} 项修改，已立即生效`);
            if (res) { settings.value = res; resetForm(); }
        }
        async function testNotify() {
            if (settingsDirty.value) { toast('请先保存设置再测试'); return; }
            const res = await guarded(api('POST', '/api/notify/test', {}));
            if (res) alert(Object.entries(res).map(([k, v]) => `${k}: ${v}`).join('\n') || '还没有选择推送渠道');
        }

        // ------------------------------------------------------------ system
        async function proxyCheck() {
            system.value.proxy_checked = false;
            await guarded(api('POST', '/api/system/proxy-check'));
            loadSystem();
        }

        onMounted(checkAuth);

        return {
            pages, page, stateFilters, auth, needLogin, password, loginError, login, logout, wsOnline,
            rooms, roomSearch, roomFilter, filteredRooms, monitoringCount, recordingCount,
            roomDialog, openRoomDialog, saveRoom, importDialog, importRooms, roomAction, bulk, deleteRoom,
            preview, openPreview, closePreview, video,
            files, fileSearch, filteredFiles, loadFiles, downloadUrl, convertFile, deleteFile,
            logs, logLevel, logFollow, logBox, filteredLogs,
            meta, settings, form, settingTab, settingGroups, specsOf, cookieSearch, filteredCookieSpecs,
            settingsDirty, hasChannel, toggleChannel, saveSettings, resetForm, testNotify,
            system, loadSystem, proxyCheck, toasts,
            duration, nextCheck, datetime, size, dirname,
        };
    },
    watch: {
        page(value) { if (value === 'files') this.loadFiles(); if (value === 'system') this.loadSystem(); },
    },
}).mount('#app');
