// booking_ristourne — bandeau PERMANENT « Ristourne appliquée » sur la commande.
//
// Le Client Script « Ristourne » (fixture) applique la remise et affiche une alerte verte qui
// disparaît en 5 secondes ; il masque en outre les champs ristourne dès que le CLIENT n'a plus de
// disponible à ce jour — y compris sur une commande validée qui, elle, a consommé sa ristourne.
// Résultat : une fois validée, rien ne disait plus qu'une remise était une ristourne.
//
// Ce fichier ne décide rien et ne modifie aucun champ : il lit et affiche.
//  - commande validée : le « Ristourne used » de la commande (journal de consommation, figé) ;
//  - brouillon : la remise posée par le Client Script tant que « Appliquer Ristourne » est sur Oui,
//    avec le rappel qu'elle ne sera consommée qu'à la validation.
//
// ⚠️ doctype_js est servi depuis le META DE FORMULAIRE MIS EN CACHE : toute évolution de ce fichier
// exige un `bench --site <site> clear-cache`.

(function () {
  const API_APPLIQUE = "booking_ristourne.sales_order.get_applied_for_sales_order";
  const TOGGLE_FIELD = "custom_appliquer_ristourne";
  const CLASSE = "ristourne-bandeau";

  function toggle_on(frm) {
    const v = frm.doc[TOGGLE_FIELD];
    return v === 1 || v === true || v === "Oui" || v === "Yes";
  }

  function retirer(frm) {
    const $box = frm.layout && frm.layout.message;
    if (!$box) return;
    // show_message EMPILE les blocs et clear_headline viderait aussi ceux d'ERPNext : on ne retire que le nôtre.
    $box.find("." + CLASSE).closest(".form-message").remove();
    if ($box.children().length === 0) $box.addClass("hidden");
  }

  function poser(frm, html, couleur) {
    retirer(frm);
    frm.layout.show_message(`<span class="${CLASSE}">${html}</span>`, couleur, true);
  }

  function montant(frm, v, currency) {
    return format_currency(flt(v), currency || frm.doc.currency);
  }

  function rafraichir(frm) {
    if (frm.is_new()) { retirer(frm); return; }

    if (frm.doc.docstatus === 0) {
      const remise = flt(frm.doc.discount_amount);
      if (toggle_on(frm) && remise > 0) {
        poser(frm,
          `🎁 <b>${__("Ristourne en cours d'application : {0}", [montant(frm, remise)])}</b> — `
          + __("posée en remise supplémentaire ; elle ne sera consommée sur le quota du client qu'à la validation."),
          "blue");
      } else {
        retirer(frm);
      }
      return;
    }

    // validée ou annulée : la vérité est dans le journal de consommation
    const commande = frm.doc.name;
    frappe.call({ method: API_APPLIQUE, args: { sales_order: commande } }).then((r) => {
      if (frm.doc.name !== commande) return;   // navigation pendant l'appel
      const m = r.message || {};
      if (!m.applied) { retirer(frm); return; }
      const lien = `<a href="/app/ristourne-used/${encodeURIComponent(m.used_doc)}">${frappe.utils.escape_html(m.used_doc)}</a>`;
      const quand = m.posting_date ? ` · ${frappe.datetime.str_to_user(String(m.posting_date).slice(0, 10))}` : "";
      poser(frm,
        `🎁 <b>${__("Ristourne appliquée sur cette commande : {0}", [montant(frm, m.applied_amount, m.currency)])}</b>`
        + ` (${lien}${quand})`,
        "green");
    }).catch(() => retirer(frm));
  }

  frappe.ui.form.on("Sales Order", {
    refresh: rafraichir,
    [TOGGLE_FIELD]: rafraichir,
    discount_amount: rafraichir,
  });
})();
