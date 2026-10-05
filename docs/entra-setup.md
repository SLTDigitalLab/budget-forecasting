# Microsoft sign-in for AI Budget Forecasting

## UI login

The application shell uses the Vision Flow-style Microsoft login: implicit
access-token response (`response_type=token`), `User.Read`, and Graph `/me`.
Redirect URIs come from `window.location.origin`.

Frontend:

```
VITE_AZURE_TENANT_ID
VITE_AZURE_CLIENT_ID
```

Protected pages require that frontend login session. Forecast generate and
overview requests do not send a Graph token. Settings reads stay available
after that login. Settings changes send the selected account's Graph token in
the Authorization header and are allowed only after Graph /me succeeds.

## Change actions

A protected Settings action first shows the in-app authentication card on the
current page: “Please authenticate to make changes”, Sign in with Microsoft,
and Cancel. Cancel performs no action and does not leave Settings. Sign in
with Microsoft reuses the existing implicit flow (`response_type=token`), not
MSAL, as a same-tab redirect. It requests `User.Read` with
`prompt=select_account`. Microsoft’s own page shows the account picker. If
the chosen account already has a browser session, Microsoft does not ask for
the password again. This application does not embed that page or collect a
Microsoft password. The redirect does not replace the normal `azureUser`
session. After Graph authorization succeeds, the intended action resumes
once. The access token is not stored in the resume record. Refreshing does
not repeat a destructive action.

The selected account's Graph access token is sent in the `Authorization`
header. The backend calls only:

`https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,userPrincipalName,department,jobTitle`

Authorization uses that Graph response and `backend/config/editor_permissions.json`.
The initial rule is department `Digital Lab` and job title `Research Executive`.
Confirm those strings against the directory. Both fields in one rule must match
exactly after trimming, ignoring case. A deployment maintainer edits the mounted
file; the next check reads it. There is no Settings screen for changing the rules.

This is an interim design. It does not validate a token issued for this API,
and it does not check issuer, audience, or tenant claims. A Graph token that
Microsoft accepts for `/me` is enough to attempt the department and job-title
check. There is no on-behalf-of exchange, client secret, or API scope.

The existing login still needs `VITE_AZURE_TENANT_ID` and `VITE_AZURE_CLIENT_ID`.
Health checks and Settings reads stay available without the change card.

## Redirect URIs

| How you run the UI | Origin used at runtime |
| --- | --- |
| Docker frontend (nginx on port 80) | `http://localhost` |
| Local Vite (`npm run dev`, port 5174, `strictPort`) | `http://localhost:5174` |

## Legacy historical workbook

On backend startup, `ORIGINAL_ACTUALS_PATH` is imported once if present and not
already registered. The uploader is stored as **Legacy import**, not the current
user.

## Logout

Logout clears the UI session and forecast state.
