import { api } from '../../api.js';
import { el } from '../../core/dom.js';
import { showError } from '../../core/errors.js';
import { BaseCard } from '../../ui/card.js';
import { Button, Mini } from '../../ui/index.js';

// Two side by side is what a comparison is. More and neither one is readable.
const COMPARE = 2;

// The dials worth putting on a banner, in the order they change the result.
const SHOWN = [
  'canonical.from_reference.weight_type',
  'canonical.from_reference.weight',
  'canonical.lora_strength',
  'canonical.controlnet.strength',
  'palette.size',
];

class KindBanner extends BaseCard {
  media() {
    const { shots = {} } = this.data;
    const shot = (role, caption) => el('figure', { className: 'kindshot' },
      shots[role]
        ? el('img', { src: api.fileUrl(shots[role]), loading: 'lazy',
                      className: 'pixel', alt: caption })
        : el('div', { className: 'kindshot-none' }),
      el('figcaption', { className: 'mini', textContent: caption }));
    return el('div', { className: 'kindshots' },
      shot('reference', 'You give it this'),
      el('span', { className: 'kindarrow', textContent: '→' }),
      shot('generated', 'It makes this'));
  }

  title() { return this.data.label; }
  subtitle() { return this.data.detail; }
  help() { return this.data.blurb; }

  rows() {
    const { dials = {}, values = {} } = this.data;
    return SHOWN.filter((path) => dials[path]).map((path) => {
      const d = dials[path];
      const set = values[path];
      return el('div', { className: 'kindrow' },
        el('div', { className: 'kindrow-head' },
          el('span', { className: 'kindrow-label', textContent: d.label }),
          el('span', { className: 'kindrow-val mono',
                       textContent: set == null ? 'default' : String(set) })),
        Mini(d.plain),
        el('div', { className: 'kindrow-arrows' },
          Mini(`More: ${d.more}`, 'kindmore'),
          Mini(`Less: ${d.less}`, 'kindless')));
    });
  }

  footer() {
    const { available, missing = [], onOpen } = this.data;
    if (!available) {
      return [Mini(`Not buildable yet — still needs ${missing.join(' and ')}.`,
                   'kindwait')];
    }
    const b = Button('Use this', { variant: 'primary', size: 'sm' });
    b.onclick = () => onOpen?.(this.data.key);
    return [b];
  }
}

export function renderKinds(host, { goTo } = {}) {
  host.replaceChildren(el('p', { className: 'ovloading', textContent: 'loading…' }));

  api.modules().then(({ modules, dials }) => {
    const keys = Object.keys(modules);
    // Pre-pick the two that can run, so the first view compares something real.
    const runnable = keys.filter((k) => modules[k].available);
    let picked = [...runnable, ...keys].slice(0, COMPARE);

    const rail = el('div', { className: 'kindrail' });
    const pair = el('div', { className: 'kindpair' });

    const draw = () => {
      rail.replaceChildren(...keys.map((key) => {
        const on = picked.includes(key);
        const b = Button(modules[key].label, {
          variant: on ? 'primary' : 'ghost', size: 'sm',
          title: modules[key].detail,
        });
        b.onclick = () => {
          picked = on ? picked.filter((k) => k !== key)
                      : [...picked, key].slice(-COMPARE);
          draw();
        };
        return b;
      }));

      pair.replaceChildren(...picked.map((key) => new KindBanner({
        data: { ...modules[key], dials,
                values: modules[key].values || {},
                onOpen: () => goTo?.('run') },
        className: 'kindbanner',
      }).render()));

      if (!picked.length) {
        pair.replaceChildren(el('p', { className: 'empty',
          textContent: 'Pick one or two above to compare them.' }));
      }
    };

    host.replaceChildren(
      Mini('Each of these makes a different kind of sprite. Pick up to two to '
           + 'see them side by side, and what each setting does to the result.'),
      rail, pair);
    draw();
  }).catch(showError);
}
