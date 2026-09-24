# Pubblicare BuildDiet e attivare il sito

Il repository GitHub esiste già: `GianlucaRoma/builddiet`. Non occorre crearne un altro né aprire un'organizzazione. La cartella locale è collegata a `origin` sul ramo `main`.

## 1. Caricare il codice

Dopo aver controllato le modifiche locali, dalla cartella del progetto:

```bash
cd builddiet
git status
git push origin main
```

Se Git chiede di autenticarsi, completa l'accesso al tuo account GitHub. Non creare ancora una release: il workflow `publish.yml` tenta di pubblicare su PyPI quando viene pubblicata una GitHub Release.

## 2. Rendere pubblico il repository

Controlla prima che codice, documenti e cronologia Git non contengano dati che vuoi tenere privati. Poi apri `https://github.com/GianlucaRoma/builddiet` e vai in **Settings → General → Danger Zone → Change repository visibility → Public**. La cronologia Git diventerà visibile insieme ai file attuali.

Per GitHub Free, [GitHub Pages richiede un repository pubblico](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site). Con GitHub Pro/Team/Enterprise si può usare anche un repository privato, ma il sito Pages resta pubblicamente accessibile.

## 3. Attivare GitHub Pages

Nel repository apri **Settings → Pages**. In **Build and deployment**, scegli **Deploy from a branch**, poi **main** e **/docs**, quindi **Save**. La pagina iniziale è `docs/index.html`. GitHub pubblicherà il sito all'indirizzo `https://gianlucaroma.github.io/builddiet/` quando il deployment sarà riuscito; controlla lo stato nella stessa pagina o in **Actions**.

Questi sono i [passaggi ufficiali per scegliere la sorgente di pubblicazione](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site). Le modifiche successive a `docs/` sul ramo `main` aggiorneranno il sito.

## Organizzazione GitHub?

Per ora resta su `GianlucaRoma/builddiet`. Un'organizzazione serve se in futuro vuoi un'identità di team, più repository o una gestione condivisa degli accessi. Non migliora il funzionamento di BuildDiet né è necessaria per Pages.
