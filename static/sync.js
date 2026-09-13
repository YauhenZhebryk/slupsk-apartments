/**
 * Słupsk Apartments — Live Sync Engine
 * 
 * Supports:
 * 1. Supabase (PostgreSQL + Realtime WebSockets for couple sync) - RECOMMENDED
 * 2. Firebase Realtime Database (Alternative cloud sync)
 * 3. Local Flask Backend (When running on Raspberry Pi / localhost)
 * 4. LocalStorage & Offline Fallback (Never lose likes or notes)
 * 5. 1-Click JSON Export & Import
 * 6. 1-Click Share Link for Girlfriend (Passes credentials via URL)
 */

class ApartmentSyncEngine {
  constructor() {
    this.STORAGE_KEY = 'slupsk_apartments_data_v2';
    this.CONFIG_KEY = 'slupsk_sync_config_v2';
    this.syncMode = 'auto'; // 'supabase' | 'firebase' | 'local_flask' | 'offline'
    this.status = 'initializing'; // 'live' | 'local' | 'offline'
    this.listeners = new Set();
    
    // Supabase
    this.supabaseClient = null;
    this.supabaseChannel = null;

    // Firebase
    this.firebaseApp = null;
    this.firebaseDb = null;
    this.firebaseRef = null;

    // Load saved cloud config
    this.config = this.loadConfig();

    // Check URL parameters for instant pairing (?sb_url=...&sb_key=...)
    this.checkUrlParams();
  }

  loadConfig() {
    try {
      const raw = localStorage.getItem(this.CONFIG_KEY);
      if (raw) return JSON.parse(raw);
    } catch (e) {
      console.warn('Failed to load sync config from localStorage', e);
    }
    return {
      provider: 'supabase', // 'supabase' | 'firebase'
      supabaseUrl: 'https://lsewnmxvgggnzlpftyes.supabase.co',
      supabaseKey: 'sb_publishable_9eULhTjVv41MdPfez5E3Rg_RIJSTtgw',
      firebaseUrl: '',
      roomKey: 'apartments'
    };
  }

  saveConfig(newConfig) {
    this.config = { ...this.config, ...newConfig };
    try {
      localStorage.setItem(this.CONFIG_KEY, JSON.stringify(this.config));
    } catch (e) {
      console.warn('Failed to save sync config', e);
    }
  }

  checkUrlParams() {
    const params = new URLSearchParams(window.location.search);
    let changed = false;

    // Supabase quick pair
    if (params.has('sb_url') && params.has('sb_key')) {
      this.config.supabaseUrl = params.get('sb_url').trim();
      this.config.supabaseKey = params.get('sb_key').trim();
      this.config.provider = 'supabase';
      changed = true;
    }

    // Firebase quick pair
    if (params.has('firebase_url')) {
      this.config.firebaseUrl = params.get('firebase_url').trim();
      this.config.provider = 'firebase';
      changed = true;
    }
    if (params.has('room')) {
      this.config.roomKey = params.get('room').trim();
      changed = true;
    }

    if (changed) {
      this.saveConfig(this.config);
      // Clean up URL without reload
      window.history.replaceState({}, document.title, window.location.pathname);
    }
  }

  onSyncUpdate(callback) {
    this.listeners.add(callback);
    return () => this.listeners.delete(callback);
  }

  notifyListeners(apartments, source) {
    this.listeners.forEach(fn => fn(apartments, source));
  }

  /**
   * Initializes the synchronization layer
   */
  async init() {
    // 1. Try Supabase if configured
    if (this.config.supabaseUrl && this.config.supabaseKey) {
      const ok = await this.initSupabase();
      if (ok) {
        this.status = 'live';
        return;
      }
    }

    // 2. Try Firebase if configured
    if (this.config.firebaseUrl) {
      const ok = await this.initFirebase();
      if (ok) {
        this.status = 'live';
        return;
      }
    }

    // 3. Check if local Flask backend is responding (Raspberry Pi)
    const isFlaskAvailable = await this.checkFlaskBackend();
    if (isFlaskAvailable) {
      this.syncMode = 'local_flask';
      this.status = 'local';
      return;
    }

    // 4. Fallback to LocalStorage / Static Seed
    this.syncMode = 'offline';
    this.status = 'offline';
  }

  async checkFlaskBackend() {
    try {
      const res = await fetch('/api/apartments?status=all', {
        headers: { 'Accept': 'application/json' },
        signal: AbortSignal.timeout(2000)
      });
      if (res.ok) {
        const data = await res.json();
        return data.success === true;
      }
    } catch (e) {
      // Local Flask not available (e.g. running on Vercel)
    }
    return false;
  }

  /**
   * Connect to Supabase
   */
  async initSupabase() {
    try {
      await this.ensureSupabaseSdk();

      let url = this.config.supabaseUrl.trim();
      url = url.replace(/\/rest\/v1\/?$/, '').replace(/\/+$/, '');
      const key = this.config.supabaseKey.trim();

      this.supabaseClient = window.supabase.createClient(url, key);

      // Listen for real-time Postgres changes
      if (this.supabaseChannel) {
        this.supabaseChannel.unsubscribe();
      }

      this.supabaseChannel = this.supabaseClient
        .channel('public:apartments_live')
        .on(
          'postgres_changes',
          { event: '*', schema: 'public', table: 'apartments' },
          async (payload) => {
            console.log('Realtime Supabase event received:', payload.eventType);
            const fresh = await this.fetchFromSupabase();
            if (fresh && fresh.length > 0) {
              this.cacheLocally(fresh);
              this.notifyListeners(fresh, 'supabase_realtime');
            }
          }
        )
        .subscribe();

      this.syncMode = 'supabase';
      this.status = 'live';
      console.log('Connected to Supabase with live realtime sync!');
      return true;
    } catch (e) {
      console.error('Failed to initialize Supabase:', e);
      return false;
    }
  }

  ensureSupabaseSdk() {
    if (window.supabase && window.supabase.createClient) {
      return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2';
      script.onload = resolve;
      script.onerror = reject;
      document.head.appendChild(script);
    });
  }

  async fetchFromSupabase() {
    if (!this.supabaseClient) return null;
    try {
      const { data, error } = await this.supabaseClient
        .from('apartments')
        .select('*')
        .order('id', { ascending: false });

      if (error) {
        console.warn('Supabase query error:', error);
        return null;
      }

      if (data && data.length > 0) {
        // Support both jsonb column schema and flattened schema
        return data.map(row => row.data || row);
      }
      return [];
    } catch (e) {
      console.warn('Supabase fetch exception:', e);
      return null;
    }
  }

  /**
   * Connect to Firebase Realtime Database
   */
  async initFirebase() {
    try {
      let dbUrl = this.config.firebaseUrl.trim();
      if (!dbUrl.startsWith('http://') && !dbUrl.startsWith('https://')) {
        dbUrl = 'https://' + dbUrl;
      }
      dbUrl = dbUrl.replace(/\/+$/, '');

      await this.ensureFirebaseSdk();

      if (!window.firebase.apps.length) {
        window.firebase.initializeApp({ databaseURL: dbUrl });
      }

      this.firebaseDb = window.firebase.database();
      const room = this.config.roomKey || 'apartments';
      this.firebaseRef = this.firebaseDb.ref(room);

      this.firebaseRef.on('value', (snapshot) => {
        const val = snapshot.val();
        if (val) {
          const list = Array.isArray(val) ? val.filter(Boolean) : Object.values(val);
          this.cacheLocally(list);
          this.notifyListeners(list, 'firebase');
        }
      });

      this.syncMode = 'firebase';
      this.status = 'live';
      return true;
    } catch (e) {
      console.error('Failed to connect to Firebase:', e);
      return false;
    }
  }

  ensureFirebaseSdk() {
    if (window.firebase && window.firebase.database) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const appScript = document.createElement('script');
      appScript.src = 'https://www.gstatic.com/firebasejs/10.9.0/firebase-app-compat.js';
      appScript.onload = () => {
        const dbScript = document.createElement('script');
        dbScript.src = 'https://www.gstatic.com/firebasejs/10.9.0/firebase-database-compat.js';
        dbScript.onload = resolve;
        dbScript.onerror = reject;
        document.head.appendChild(dbScript);
      };
      appScript.onerror = reject;
      document.head.appendChild(appScript);
    });
  }

  /**
   * Load apartments from current active provider
   */
  async getApartments() {
    // 1. Supabase
    if (this.syncMode === 'supabase' && this.supabaseClient) {
      const list = await this.fetchFromSupabase();
      if (list && list.length > 0) {
        this.cacheLocally(list);
        return list;
      }
      // If Supabase table is empty, auto-seed it with our 20 apartments!
      const seed = await this.loadBundledSeed();
      if (seed && seed.length > 0) {
        await this.uploadInitialSeedToSupabase(seed);
        this.cacheLocally(seed);
        return seed;
      }
    }

    // 2. Firebase
    if (this.syncMode === 'firebase' && this.firebaseRef) {
      try {
        const snapshot = await this.firebaseRef.once('value');
        const val = snapshot.val();
        if (val) {
          const list = Array.isArray(val) ? val.filter(Boolean) : Object.values(val);
          this.cacheLocally(list);
          return list;
        }
      } catch (e) {
        console.warn('Firebase read error:', e);
      }
    }

    // 3. Local Flask backend
    if (this.syncMode === 'local_flask') {
      try {
        const res = await fetch('/api/apartments');
        const data = await res.json();
        if (data.success && data.apartments) {
          this.cacheLocally(data.apartments);
          return data.apartments;
        }
      } catch (e) {
        console.warn('Local Flask error:', e);
      }
    }

    // 4. LocalStorage cache
    const cached = this.getLocalCache();
    if (cached && cached.length > 0) {
      return cached;
    }

    // 5. Bundled seed JSON
    return await this.loadBundledSeed();
  }

  async loadBundledSeed() {
    try {
      const res = await fetch('./static/apartments_seed.json');
      if (res.ok) {
        const seed = await res.json();
        this.cacheLocally(seed);
        return seed;
      }
    } catch (e) {
      console.warn('Could not load static seed JSON', e);
    }
    return [];
  }

  async uploadInitialSeedToSupabase(seed) {
    if (!this.supabaseClient) return;
    try {
      const rows = seed.map(item => ({
        id: item.id,
        data: item
      }));
      const { error } = await this.supabaseClient.from('apartments').upsert(rows);
      if (!error) {
        console.log('Supabase successfully seeded with initial apartments.');
      } else {
        console.warn('Supabase auto-seed warning:', error);
      }
    } catch (e) {
      console.warn('Supabase auto-seed failed:', e);
    }
  }

  /**
   * Update specific fields of an apartment (status, notes, rating, etc.)
   */
  async updateApartmentField(aptId, fields, currentList) {
    const list = currentList || this.getLocalCache() || [];
    const item = list.find(a => a.id === aptId);
    if (item) {
      Object.assign(item, fields);
      this.cacheLocally(list);
      this.notifyListeners(list, 'optimistic');
    }

    // Supabase
    if (this.syncMode === 'supabase' && this.supabaseClient && item) {
      try {
        await this.supabaseClient
          .from('apartments')
          .upsert({ id: aptId, data: item });
        return true;
      } catch (e) {
        console.error('Supabase update failed:', e);
      }
    }

    // Firebase
    if (this.syncMode === 'firebase' && this.firebaseRef) {
      try {
        await this.firebaseRef.child(String(aptId)).update(fields);
        return true;
      } catch (e) {
        console.error('Firebase update failed:', e);
      }
    }

    // Local Flask
    if (this.syncMode === 'local_flask') {
      try {
        if ('status' in fields) {
          await fetch(`/api/apartments/${aptId}/status`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: fields.status })
          });
        }
        if ('notes' in fields) {
          await fetch(`/api/apartments/${aptId}/notes`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ notes: fields.notes })
          });
        }
        if ('user_rating' in fields) {
          await fetch(`/api/apartments/${aptId}/rate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_rating: fields.user_rating })
          });
        }
        return true;
      } catch (e) {
        console.error('Local Flask update failed:', e);
      }
    }

    return true;
  }

  /**
   * Save a full apartment object (create or update)
   */
  async saveApartment(aptData, currentList) {
    const list = currentList || this.getLocalCache() || [];
    let savedApt = { ...aptData };

    if (!savedApt.id) {
      savedApt.id = Date.now();
      savedApt.created_at = new Date().toISOString();
    }
    savedApt.updated_at = new Date().toISOString();

    const idx = list.findIndex(a => a.id === savedApt.id);
    if (idx >= 0) {
      list[idx] = savedApt;
    } else {
      list.unshift(savedApt);
    }
    this.cacheLocally(list);
    this.notifyListeners(list, 'local');

    if (this.syncMode === 'supabase' && this.supabaseClient) {
      try {
        await this.supabaseClient
          .from('apartments')
          .upsert({ id: savedApt.id, data: savedApt });
      } catch (e) {
        console.error('Supabase save failed:', e);
      }
    }

    if (this.syncMode === 'firebase' && this.firebaseRef) {
      try {
        await this.firebaseRef.child(String(savedApt.id)).set(savedApt);
      } catch (e) {
        console.error('Firebase save failed:', e);
      }
    }

    if (this.syncMode === 'local_flask') {
      try {
        const method = idx >= 0 ? 'PUT' : 'POST';
        const url = idx >= 0 ? `/api/apartments/${savedApt.id}` : '/api/apartments';
        await fetch(url, {
          method,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(savedApt)
        });
      } catch (e) {
        console.error('Flask save failed:', e);
      }
    }

    return savedApt;
  }

  /**
   * Delete an apartment
   */
  async deleteApartment(aptId, currentList) {
    const list = (currentList || this.getLocalCache() || []).filter(a => a.id !== aptId);
    this.cacheLocally(list);
    this.notifyListeners(list, 'local');

    if (this.syncMode === 'supabase' && this.supabaseClient) {
      try {
        await this.supabaseClient
          .from('apartments')
          .delete()
          .eq('id', aptId);
      } catch (e) {
        console.error('Supabase delete failed:', e);
      }
    }

    if (this.syncMode === 'firebase' && this.firebaseRef) {
      try {
        await this.firebaseRef.child(String(aptId)).remove();
      } catch (e) {
        console.error('Firebase delete failed:', e);
      }
    }

    if (this.syncMode === 'local_flask') {
      try {
        await fetch(`/api/apartments/${aptId}`, { method: 'DELETE' });
      } catch (e) {
        console.error('Flask delete failed:', e);
      }
    }

    return true;
  }

  cacheLocally(list) {
    try {
      localStorage.setItem(this.STORAGE_KEY, JSON.stringify(list));
    } catch (e) {
      console.warn('Failed to cache in localStorage', e);
    }
  }

  getLocalCache() {
    try {
      const raw = localStorage.getItem(this.STORAGE_KEY);
      if (raw) return JSON.parse(raw);
    } catch (e) {
      console.warn('Failed to read from localStorage', e);
    }
    return null;
  }

  exportData(list) {
    const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(list || this.getLocalCache() || [], null, 2));
    const a = document.createElement('a');
    a.setAttribute('href', dataStr);
    a.setAttribute('download', `slupsk_apartments_backup_${new Date().toISOString().slice(0,10)}.json`);
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  async importData(jsonContent) {
    try {
      const parsed = typeof jsonContent === 'string' ? JSON.parse(jsonContent) : jsonContent;
      const list = Array.isArray(parsed) ? parsed : (parsed.apartments || []);
      if (!list || list.length === 0) throw new Error('Файл не содержит квартир');

      this.cacheLocally(list);
      this.notifyListeners(list, 'import');

      if (this.syncMode === 'supabase' && this.supabaseClient) {
        await this.uploadInitialSeedToSupabase(list);
      }
      return { success: true, count: list.length };
    } catch (e) {
      return { success: false, error: e.message };
    }
  }

  getShareUrl() {
    const url = new URL(window.location.origin + window.location.pathname);
    if (this.config.supabaseUrl && this.config.supabaseKey) {
      url.searchParams.set('sb_url', this.config.supabaseUrl);
      url.searchParams.set('sb_key', this.config.supabaseKey);
    } else if (this.config.firebaseUrl) {
      url.searchParams.set('firebase_url', this.config.firebaseUrl);
      if (this.config.roomKey && this.config.roomKey !== 'apartments') {
        url.searchParams.set('room', this.config.roomKey);
      }
    }
    return url.toString();
  }
}

window.syncEngine = new ApartmentSyncEngine();
