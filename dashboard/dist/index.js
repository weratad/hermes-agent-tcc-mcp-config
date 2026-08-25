/* TCC MCP Config — dashboard tab.
   Plain IIFE, no build step. React comes from the plugin SDK.
   Apple-style restraint: minimal copy, quiet hierarchy, generous spacing.
   Styling is a self-contained <style> block scoped to .tmc- classes (theme
   CSS vars for light/dark), independent of the dashboard's compiled Tailwind. */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  var React = SDK.React;
  var h = React.createElement;
  var useState = SDK.hooks.useState;
  var useEffect = SDK.hooks.useEffect;
  var useCallback = SDK.hooks.useCallback;
  var C = SDK.components;

  var BASE = "/api/plugins/tcc-mcp-config";

  // Bump whenever this file changes — the badge is the fastest stale-bundle tell.
  var BUILD = "v2.4.1";

  var PLACEHOLDER = "https://api.example.com/mcp";

  var CSS = [
    ".tmc { --tmc-fg: hsl(var(--foreground)); --tmc-mut: hsl(var(--muted-foreground));",
    "       --tmc-bd: hsl(var(--border)); --tmc-mut-bg: hsl(var(--muted) / 0.45);",
    "       --tmc-des: hsl(var(--destructive)); }",
    ".tmc-title { font-size: 19px; font-weight: 600; letter-spacing: -0.02em; margin: 0; color: var(--tmc-fg); }",
    ".tmc-sub { font-size: 12.5px; line-height: 1.5; margin: 5px 0 0; color: var(--tmc-mut); }",
    ".tmc-label { font-size: 10.5px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--tmc-mut); }",
    ".tmc-hint { font-size: 12px; line-height: 1.4; color: var(--tmc-mut); }",
    ".tmc-env { font-size: 15px; font-weight: 600; letter-spacing: -0.01em; color: var(--tmc-fg); }",
    ".tmc-envsub { font-size: 11.5px; color: var(--tmc-mut); margin-top: 3px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }",
    ".tmc-field { display: flex; flex-direction: column; gap: 7px; }",
    ".tmc-list { border: 1px solid var(--tmc-bd); border-radius: 10px; overflow: hidden; }",
    ".tmc-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 9px 12px; transition: background-color 130ms ease; }",
    ".tmc-row + .tmc-row { border-top: 1px solid var(--tmc-bd); }",
    ".tmc-row:hover { background-color: var(--tmc-mut-bg); }",
    ".tmc-name { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; color: var(--tmc-fg); }",
    ".tmc-meta { font-size: 11px; color: var(--tmc-mut); white-space: nowrap; }",
    ".tmc-empty { font-size: 12.5px; color: var(--tmc-mut); padding: 16px 12px; text-align: center; }",
    ".tmc-alert { font-size: 12.5px; line-height: 1.5; color: var(--tmc-des); background: hsl(var(--destructive) / 0.08); border: 1px solid hsl(var(--destructive) / 0.28); border-radius: 10px; padding: 11px 13px; }",
    ".tmc-foot { font-size: 12px; line-height: 1.5; color: var(--tmc-mut); }",
    ".tmc-code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11.5px; color: var(--tmc-fg); background: var(--tmc-mut-bg); border: 1px solid var(--tmc-bd); border-radius: 7px; padding: 2px 7px; }",
    ".tmc-count { font-size: 11.5px; color: var(--tmc-mut); font-variant-numeric: tabular-nums; }",
    ".tmc-users { font-size: 11.5px; color: var(--tmc-mut); white-space: nowrap; }",
    ".tmc-users-n { font-weight: 600; color: var(--tmc-fg); font-variant-numeric: tabular-nums; }"
  ].join("\n");

  function call(path, method, body) {
    return SDK.fetchJSON(BASE + path, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined
    });
  }

  function errText(err) {
    return String((err && (err.message || err.detail)) || err);
  }

  function Field(label, control, hint) {
    return h("div", { className: "tmc-field" },
      h("div", { className: "tmc-label" }, label),
      control,
      hint ? h("div", { className: "tmc-hint" }, hint) : null);
  }

  function SettingsCard(props) {
    var data = props.data;

    var [url, setUrl] = useState(data.url || "");
    var [mcpKey, setMcpKey] = useState("");
    var [gatewayKey, setGatewayKey] = useState("");
    var [busy, setBusy] = useState("");
    var [note, setNote] = useState(null);
    var [tools, setTools] = useState(null);

    useEffect(function () { setUrl(data.url || ""); }, [data.url]);

    function done(kind, text) {
      setNote({ kind: kind, text: text });
      setBusy("");
    }

    var save = useCallback(function () {
      setBusy("save");
      setNote(null);
      call("/settings", "PUT", {
        url: url,
        mcp_api_key: mcpKey || null,
        gateway_key: gatewayKey || null
      }).then(function (res) {
        setMcpKey("");
        setGatewayKey("");
        var extra = res.resynced_profiles
          ? " · resynced " + res.resynced_profiles
          : "";
        if (res.restart_required) extra += " · restart gateway";
        done("ok", "Saved" + extra);
        props.onChanged();
      }).catch(function (err) { done("error", errText(err)); });
    }, [url, mcpKey, gatewayKey, props]);

    var test = useCallback(function () {
      setBusy("test"); setNote(null); setTools(null);
      call("/test", "POST")
        .then(function (res) {
          if (res.ok && res.tools && res.tools.length) setTools(res.tools);
          done(res.ok ? "ok" : "error", res.message);
        })
        .catch(function (err) { done("error", errText(err)); });
    }, []);

    var badge = data.live
      ? h(C.Badge, null, "Live")
      : data.configured
        ? h(C.Badge, { variant: "destructive" }, "Restart")
        : h(C.Badge, { variant: "outline" }, "Not set");

    return h(C.Card, { className: data.live ? "border-primary" : "" },
      h(C.CardContent, {
        style: { padding: "20px", display: "flex", flexDirection: "column", gap: "18px" }
      },
        h("div", { className: "flex items-start justify-between gap-2" },
          h("div", { className: "min-w-0" },
            h("div", { className: "tmc-env" }, "tcc-api MCP"),
            h("div", { className: "tmc-envsub" }, data.mcp_server_name || "tcc-api")),
          badge),

        data.configured && !data.live
          ? h("div", { className: "tmc-alert" }, "Saved — restart gateway to apply")
          : null,

        Field("MCP URL",
          h(C.Input, {
            value: url, placeholder: PLACEHOLDER,
            onChange: function (e) { setUrl(e.target.value); }
          })),

        Field("MCP API Key",
          h(C.Input, {
            type: "password", value: mcpKey,
            placeholder: data.mcp_key_set
              ? "Saved " + data.mcp_key_hint + " — blank to keep"
              : "Not set",
            onChange: function (e) { setMcpKey(e.target.value); }
          })),

        Field("Gateway Key",
          h(C.Input, {
            type: "password", value: gatewayKey,
            placeholder: data.gateway_key_set
              ? "Saved " + data.gateway_key_hint + " — blank to keep"
              : "Not set",
            onChange: function (e) { setGatewayKey(e.target.value); }
          }),
          data.gateway_key_weak
            ? h("span", { style: { color: "hsl(var(--destructive))" } }, "Too short — will be rejected")
            : null),

        h("div", { className: "flex flex-wrap gap-2" },
          h(C.Button, { onClick: save, disabled: !!busy },
            busy === "save" ? "Saving…" : "Save"),
          h(C.Button, {
            variant: "ghost", onClick: test,
            disabled: !!busy || !data.mcp_key_set || !data.url
          }, busy === "test" ? "Testing…" : "Test connection")),

        note ? h("div", {
          style: {
            fontSize: "13px",
            color: note.kind === "ok" ? "hsl(142 71% 45%)" : "hsl(var(--destructive))"
          }
        }, note.text) : null,

        tools ? h("div", { className: "tmc-list", style: { padding: "10px 12px" } },
          h("div", { className: "tmc-label", style: { marginBottom: "6px" } },
            "Tools (" + tools.length + ")"),
          h("div", { style: { fontSize: "11.5px", lineHeight: 1.6, wordBreak: "break-all", color: "hsl(var(--muted-foreground))" } },
            tools.join(" · "))) : null
      )
    );
  }

  function ProfilesCard(props) {
    var totalHint = Number(props.total || 0);
    var [pq, setPq] = useState("");
    var [ppage, setPpage] = useState(1);
    var [pdata, setPdata] = useState(null);
    var [ploading, setPloading] = useState(false);

    var loadProfiles = useCallback(function () {
      setPloading(true);
      var qs = "?q=" + encodeURIComponent(pq) +
        "&page=" + ppage + "&page_size=10";
      SDK.fetchJSON(BASE + "/profiles" + qs)
        .then(function (res) { setPdata(res); setPloading(false); })
        .catch(function () { setPdata(null); setPloading(false); });
    }, [pq, ppage]);

    useEffect(function () { loadProfiles(); }, [loadProfiles]);

    var count = pdata ? pdata.total : totalHint;

    return h(C.Card, null,
      h(C.CardContent, {
        style: { padding: "20px", display: "flex", flexDirection: "column", gap: "14px" }
      },
        h("div", { className: "flex items-start justify-between gap-2" },
          h("div", { className: "min-w-0" },
            h("div", { className: "tmc-env" }, "Profiles"),
            h("div", { className: "tmc-envsub" }, "staff-<id> / user-<id>[-store-<id>]")),
          h("div", { className: "tmc-users" },
            h("span", { className: "tmc-users-n" }, Number(count || 0).toLocaleString()),
            " users")),

        h(C.Input, {
          value: pq, placeholder: "Search staff-3, user-42…",
          onChange: function (e) { setPq(e.target.value); setPpage(1); }
        }),
        h("div", { className: "tmc-list" },
          ploading
            ? h("div", { className: "tmc-empty" }, "Loading…")
            : (pdata && pdata.items && pdata.items.length)
              ? pdata.items.map(function (p) {
                  return h("div", { key: p.name, className: "tmc-row" },
                    h("span", { className: "tmc-name" }, p.name),
                    h("span", { className: "tmc-meta" },
                      (p.type === "staff" ? "Staff" : p.type === "organizer" ? "Organizer" : "User") + " #" + p.id +
                      (p.store ? " · store " + p.store : "")));
                })
              : h("div", { className: "tmc-empty" }, pq ? "No matches" : "No profiles yet")),
        pdata && pdata.pages > 1
          ? h("div", { className: "flex items-center justify-between" },
              h("span", { className: "tmc-count" }, pdata.page + " / " + pdata.pages),
              h("div", { className: "flex gap-1.5" },
                h(C.Button, {
                  variant: "outline", size: "sm", disabled: pdata.page <= 1,
                  onClick: function () { setPpage(Math.max(1, ppage - 1)); }
                }, "Prev"),
                h(C.Button, {
                  variant: "outline", size: "sm", disabled: pdata.page >= pdata.pages,
                  onClick: function () { setPpage(ppage + 1); }
                }, "Next")))
          : null)
    );
  }

  function Page() {
    var [state, setState] = useState(null);
    var [error, setError] = useState(null);

    var load = useCallback(function () {
      SDK.fetchJSON(BASE + "/settings")
        .then(function (res) { setState(res); setError(null); })
        .catch(function (err) { setError(errText(err)); });
    }, []);

    useEffect(function () { load(); }, [load]);

    var style = h("style", null, CSS);

    if (error) {
      return h("div", { className: "tmc" }, style,
        h(C.Card, null, h(C.CardContent, { style: { padding: "20px" } },
          h("p", { style: { fontSize: "13px", color: "hsl(var(--destructive))" } },
            "Failed to load: " + error))));
    }
    if (!state) {
      return h("div", { className: "tmc" }, style,
        h(C.Card, null, h(C.CardContent, { style: { padding: "24px" } },
          h("p", { className: "tmc-hint" }, "Loading…"))));
    }

    var pending = !!state.restart_required;

    return h("div", { className: "tmc", style: { display: "flex", flexDirection: "column", gap: "22px" } },
      style,

      h("div", null,
        h("div", { className: "flex items-center justify-between gap-2" },
          h("h2", { className: "tmc-title" }, "TCC MCP Config"),
          h(C.Badge, { variant: "outline" }, BUILD)),
        h("p", { className: "tmc-sub" },
          "One MCP · per-user memory profiles")),

      pending ? h("div", { className: "tmc-alert" },
        "Restart gateway — still on old values  ",
        h("span", { className: "tmc-code" }, state.restart_command)) : null,

      h(SettingsCard, { data: state, onChanged: load }),
      h(ProfilesCard, { total: state.profiles }),

      h("p", { className: "tmc-foot" },
        "Changes apply to existing profiles at once · MCP URL/key changes need a restart")
    );
  }

  window.__HERMES_PLUGINS__.register("tcc-mcp-config", Page);
})();
