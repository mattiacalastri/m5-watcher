"""
m5-watcher — Template canonico per nuovo TabPane.
sess.1895: forgiato dopo audit che ha trovato radar + tgbots SENZA border box
(disarmonia visiva cross-tab). Ogni nuovo tab DEVE seguire questo schema.

CHECKLIST OBBLIGATORIA (lint script: ./scripts/lint_tab_boxes.py):
1. TabPane ha id univoco "tab-<name>"
2. Container interno è ScrollableContainer con id "<name>-scroll"
3. CSS contiene regola "#<name>-scroll" con border heavy {TEAL}
4. Static header opzionale ma se presente segue pattern bold + italic dim

Copy-paste pattern (incolla in app.py dentro `with TabbedContent`):

    with TabPane("🆕 NewTab", id="tab-newtab"):
        with ScrollableContainer(id="newtab-scroll"):
            yield Static(
                f"[bold {ACCENT_COLOR}]🆕 NEW TAB TITLE[/]  [{DIM}]· subtitle[/]\n"
                f"[italic {DIM}]Poetic one-liner (Polpo voice).[/]",
                id="newtab-header")
            yield Static("", id="newtab-content")

CSS pattern (incolla in CSS string blocco styles):

    #newtab-scroll {{
        background: {BG_ALT};
        border: heavy {TEAL};
        border-title-color: {ACCENT_COLOR};
        padding: 1 3;
        height: 1fr;
    }}

Refresh hook (in `_refresh_slow`):

    if _active_tab == "tab-newtab":
        self._update_if_changed("newtab-content", new_widget.render(self._newtab_data))
"""
