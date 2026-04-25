/*
 * Scout Genius — XSS-safe DOM helpers for rendering AI / Bullhorn / server-derived content.
 *
 * Why this file exists
 * --------------------
 * Many recruiter-facing dashboards (Scout Screening, Scout Vetting Sandbox, Scout Support,
 * Scout Prospector, ATS Integration, Knowledge Hub, etc.) render data that originated from
 * GPT-5.4 prompt outputs, Bullhorn API responses, or applicant-supplied fields. Using
 * `element.innerHTML = '...' + serverData + '...'` (or template-literal equivalent) is an
 * unconditional XSS sink — any HTML/JS in the upstream data executes in the recruiter's
 * browser session under the app origin.
 *
 * These helpers wrap the safe DOM primitives (createElement / textContent / appendChild)
 * so call sites read as concisely as the unsafe innerHTML version, but every interpolated
 * value is text-only by construction. Class names and URL schemes are also defensively
 * sanitized to prevent CSS/JS injection.
 *
 * Public API (exposed on window.AIOutput):
 *   clear(node)
 *   safeRenderText(node, text)
 *   safeRenderAlert(node, { variant, icon, text })
 *   safeRenderDismissibleAlert(node, { variant, text })   // alert + close button
 *   safeRenderBadge(node, { variant, icon, text })
 *   safeRenderIconText(node, { iconClass, text, textClass })
 *   safeAppendKeyValue(node, label, value)            // appends, does not clear
 *   safeRenderList(node, items, listClass)
 *   safeBuildLink(href, label, { className, target }) // returns Element
 *   safeBuildHighlightedText(node, fullText, query)   // appends text + <mark> for match
 *   isSafeUrl(url)
 *
 * Sanitization invariants
 * -----------------------
 *   - Every text input is coerced via String() before insertion as a text node.
 *   - Class-name inputs are stripped to [a-zA-Z0-9_\- ] to prevent attribute breakout.
 *   - Icon inputs are stripped to [a-zA-Z0-9\- ] (FontAwesome class shape).
 *   - URL inputs must start with /, http://, https://, mailto:, tel:, or # — anything
 *     else (including javascript:, data:, vbscript:) is rewritten to '#'.
 *
 * This file MUST NOT use `.innerHTML =`, `document.write`, `eval`, `new Function`,
 * `setTimeout(string)`, or `setInterval(string)`. The audit test enforces this.
 */
(function (window) {
    'use strict';

    if (!window || !window.document) {
        return;
    }

    var CLASS_RE = /[^a-zA-Z0-9_\- ]/g;
    var ICON_RE = /[^a-zA-Z0-9\- ]/g;
    var TARGET_RE = /[^a-zA-Z_]/g;

    function toStr(v) {
        return v === null || v === undefined ? '' : String(v);
    }

    function sanitizeClass(v) {
        return toStr(v).replace(CLASS_RE, '').trim();
    }

    function sanitizeIcon(v) {
        return toStr(v).replace(ICON_RE, '').trim();
    }

    function sanitizeTarget(v) {
        return toStr(v).replace(TARGET_RE, '').trim();
    }

    function clear(node) {
        if (!node) return;
        while (node.firstChild) {
            node.removeChild(node.firstChild);
        }
    }

    function safeRenderText(node, text) {
        if (!node) return;
        clear(node);
        node.appendChild(document.createTextNode(toStr(text)));
    }

    function safeRenderAlert(node, options) {
        if (!node) return;
        clear(node);
        var opts = options || {};
        var variant = sanitizeClass(opts.variant) || 'info';
        var div = document.createElement('div');
        div.className = 'alert alert-' + variant;
        if (opts.icon) {
            var i = document.createElement('i');
            i.className = 'fas ' + sanitizeIcon(opts.icon) + ' me-2';
            div.appendChild(i);
        }
        div.appendChild(document.createTextNode(toStr(opts.text)));
        node.appendChild(div);
    }

    function safeRenderDismissibleAlert(node, options) {
        if (!node) return;
        clear(node);
        var opts = options || {};
        var variant = sanitizeClass(opts.variant) || 'info';
        var div = document.createElement('div');
        div.className = 'alert alert-' + variant + ' alert-dismissible fade show';
        div.setAttribute('role', 'alert');
        div.appendChild(document.createTextNode(toStr(opts.text)));
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn-close';
        btn.setAttribute('data-bs-dismiss', 'alert');
        btn.setAttribute('aria-label', 'Close');
        div.appendChild(btn);
        node.appendChild(div);
        return div;
    }

    function safeRenderBadge(node, options) {
        if (!node) return;
        clear(node);
        var opts = options || {};
        var variant = sanitizeClass(opts.variant) || 'secondary';
        var span = document.createElement('span');
        span.className = 'badge bg-' + variant;
        if (opts.icon) {
            var i = document.createElement('i');
            i.className = 'fas ' + sanitizeIcon(opts.icon) + ' me-1';
            span.appendChild(i);
        }
        span.appendChild(document.createTextNode(toStr(opts.text)));
        node.appendChild(span);
    }

    function safeRenderIconText(node, options) {
        if (!node) return;
        clear(node);
        var opts = options || {};
        var container = node;
        if (opts.textClass) {
            var span = document.createElement('span');
            span.className = sanitizeClass(opts.textClass);
            node.appendChild(span);
            container = span;
        }
        if (opts.iconClass) {
            var i = document.createElement('i');
            i.className = sanitizeClass(opts.iconClass) + ' me-1';
            container.appendChild(i);
        }
        container.appendChild(document.createTextNode(toStr(opts.text)));
    }

    function safeAppendKeyValue(node, label, value) {
        if (!node) return;
        var p = document.createElement('p');
        p.className = 'mb-1';
        var strong = document.createElement('strong');
        strong.appendChild(document.createTextNode(toStr(label) + ': '));
        p.appendChild(strong);
        p.appendChild(document.createTextNode(toStr(value)));
        node.appendChild(p);
    }

    function safeRenderList(node, items, listClass) {
        if (!node) return;
        clear(node);
        var ul = document.createElement('ul');
        var cls = sanitizeClass(listClass);
        if (cls) ul.className = cls;
        var arr = items || [];
        for (var idx = 0; idx < arr.length; idx++) {
            var li = document.createElement('li');
            li.appendChild(document.createTextNode(toStr(arr[idx])));
            ul.appendChild(li);
        }
        node.appendChild(ul);
    }

    function isSafeUrl(url) {
        if (typeof url !== 'string') return false;
        var trimmed = url.trim().toLowerCase();
        if (trimmed.length === 0) return false;
        return (
            trimmed.charAt(0) === '/' ||
            trimmed.charAt(0) === '#' ||
            trimmed.indexOf('http://') === 0 ||
            trimmed.indexOf('https://') === 0 ||
            trimmed.indexOf('mailto:') === 0 ||
            trimmed.indexOf('tel:') === 0
        );
    }

    function safeBuildHighlightedText(node, fullText, query) {
        if (!node) return;
        var text = toStr(fullText);
        var q = toStr(query);
        if (!q) {
            node.appendChild(document.createTextNode(text));
            return;
        }
        var idx = text.toLowerCase().indexOf(q.toLowerCase());
        if (idx === -1) {
            node.appendChild(document.createTextNode(text));
            return;
        }
        if (idx > 0) {
            node.appendChild(document.createTextNode(text.substring(0, idx)));
        }
        var mark = document.createElement('mark');
        mark.appendChild(document.createTextNode(text.substring(idx, idx + q.length)));
        node.appendChild(mark);
        var tail = idx + q.length;
        if (tail < text.length) {
            node.appendChild(document.createTextNode(text.substring(tail)));
        }
    }

    function safeBuildLink(href, label, opts) {
        var a = document.createElement('a');
        a.href = isSafeUrl(href) ? href : '#';
        a.appendChild(document.createTextNode(toStr(label)));
        var o = opts || {};
        if (o.className) {
            var cls = sanitizeClass(o.className);
            if (cls) a.className = cls;
        }
        if (o.target) {
            var tgt = sanitizeTarget(o.target);
            if (tgt) a.target = tgt;
        }
        return a;
    }

    window.AIOutput = {
        clear: clear,
        safeRenderText: safeRenderText,
        safeRenderAlert: safeRenderAlert,
        safeRenderDismissibleAlert: safeRenderDismissibleAlert,
        safeRenderBadge: safeRenderBadge,
        safeRenderIconText: safeRenderIconText,
        safeAppendKeyValue: safeAppendKeyValue,
        safeRenderList: safeRenderList,
        safeBuildLink: safeBuildLink,
        safeBuildHighlightedText: safeBuildHighlightedText,
        isSafeUrl: isSafeUrl
    };
})(typeof window !== 'undefined' ? window : null);
