/**
 * Pan / zoom viewport for large focus-tree PNGs.
 * Initial view: fit image height into the viewport (readable band), left-aligned.
 */
(function (global) {
  "use strict";

  const MIN_SCALE = 0.05;
  const MAX_SCALE = 4;
  const ZOOM_STEP = 1.15;

  function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v));
  }

  class FocusPanZoom {
    /**
     * @param {HTMLElement} root  element containing .pz-viewport > .pz-stage > img
     *   (toolbar buttons .pz-* may live anywhere under root)
     */
    constructor(root) {
      this.root = root;
      this.viewport = root.querySelector(".pz-viewport");
      this.stage = root.querySelector(".pz-stage");
      this.img = root.querySelector(".pz-stage img") || root.querySelector("img");
      this.scaleLabel = root.querySelector(".pz-scale");
      this.scale = 1;
      this.tx = 0;
      this.ty = 0;
      this._drag = null;
      this._nw = 0;
      this._nh = 0;
      this._bound = false;
      if (!this.viewport || !this.stage || !this.img) {
        throw new Error("FocusPanZoom: missing .pz-viewport / .pz-stage / img");
      }
      this._bind();
    }

    _bind() {
      if (this._bound) return;
      this._bound = true;

      this.viewport.addEventListener(
        "wheel",
        (e) => {
          e.preventDefault();
          const rect = this.viewport.getBoundingClientRect();
          const mx = e.clientX - rect.left;
          const my = e.clientY - rect.top;
          const factor = e.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
          this.zoomAt(mx, my, factor);
        },
        { passive: false }
      );

      this.viewport.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        this.viewport.setPointerCapture(e.pointerId);
        this._drag = {
          id: e.pointerId,
          x: e.clientX,
          y: e.clientY,
          tx: this.tx,
          ty: this.ty,
        };
        this.viewport.classList.add("dragging");
      });

      this.viewport.addEventListener("pointermove", (e) => {
        if (!this._drag || this._drag.id !== e.pointerId) return;
        this.tx = this._drag.tx + (e.clientX - this._drag.x);
        this.ty = this._drag.ty + (e.clientY - this._drag.y);
        this._apply();
      });

      const endDrag = (e) => {
        if (!this._drag || this._drag.id !== e.pointerId) return;
        this._drag = null;
        this.viewport.classList.remove("dragging");
      };
      this.viewport.addEventListener("pointerup", endDrag);
      this.viewport.addEventListener("pointercancel", endDrag);

      this.viewport.addEventListener("dblclick", (e) => {
        const rect = this.viewport.getBoundingClientRect();
        this.zoomAt(e.clientX - rect.left, e.clientY - rect.top, ZOOM_STEP * ZOOM_STEP);
      });

      const btn = (sel, fn) => {
        const el = this.root.querySelector(sel);
        if (el) el.addEventListener("click", (ev) => {
          ev.preventDefault();
          fn();
        });
      };
      btn(".pz-zoom-in", () => this.zoomCenter(ZOOM_STEP));
      btn(".pz-zoom-out", () => this.zoomCenter(1 / ZOOM_STEP));
      btn(".pz-fit", () => this.fitContain());
      btn(".pz-fit-height", () => this.fitHeight());
      btn(".pz-reset", () => this.fitHeight());
    }

    /**
     * Load a new image URL; resolves when natural size is known and view is ready.
     * @param {string} url
     * @param {string} [alt]
     * @returns {Promise<void>}
     */
    load(url, alt) {
      return new Promise((resolve, reject) => {
        this.img.onload = () => {
          this._nw = this.img.naturalWidth || 0;
          this._nh = this.img.naturalHeight || 0;
          this.fitHeight();
          resolve();
        };
        this.img.onerror = () => reject(new Error("image load failed"));
        if (alt != null) this.img.alt = alt;
        this.img.removeAttribute("src");
        this.img.src = url;
      });
    }

    clear() {
      this.img.removeAttribute("src");
      this.img.alt = "";
      this._nw = 0;
      this._nh = 0;
      this.scale = 1;
      this.tx = 0;
      this.ty = 0;
      this._apply();
    }

    _vw() {
      return this.viewport.clientWidth || 1;
    }

    _vh() {
      return this.viewport.clientHeight || 1;
    }

    _apply() {
      this.stage.style.transform =
        `translate(${this.tx}px, ${this.ty}px) scale(${this.scale})`;
      if (this.scaleLabel) {
        this.scaleLabel.textContent = `${Math.round(this.scale * 100)}%`;
      }
    }

    zoomAt(mx, my, factor) {
      const next = clamp(this.scale * factor, MIN_SCALE, MAX_SCALE);
      if (next === this.scale) return;
      const worldX = (mx - this.tx) / this.scale;
      const worldY = (my - this.ty) / this.scale;
      this.scale = next;
      this.tx = mx - worldX * this.scale;
      this.ty = my - worldY * this.scale;
      this._apply();
    }

    zoomCenter(factor) {
      this.zoomAt(this._vw() / 2, this._vh() / 2, factor);
    }

    /** Entire image visible (may be a thin strip for ultra-wide trees). */
    fitContain() {
      if (!this._nw || !this._nh) return;
      const s = Math.min(this._vw() / this._nw, this._vh() / this._nh);
      this.scale = clamp(s, MIN_SCALE, MAX_SCALE);
      this.tx = (this._vw() - this._nw * this.scale) / 2;
      this.ty = (this._vh() - this._nh * this.scale) / 2;
      this._apply();
    }

    /**
     * Match image height to viewport; left-align (best default for wide trees).
     */
    fitHeight() {
      if (!this._nw || !this._nh) return;
      const s = this._vh() / this._nh;
      this.scale = clamp(s, MIN_SCALE, MAX_SCALE);
      this.tx = 12;
      this.ty = (this._vh() - this._nh * this.scale) / 2;
      this._apply();
    }
  }

  global.FocusPanZoom = FocusPanZoom;
})(typeof window !== "undefined" ? window : globalThis);
