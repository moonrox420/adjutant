"use client";

import { AccountAccess } from "../../components/account";

export default function AccountPage() {
  return <AccountAccess signedIn={() => window.location.replace("/")} />;
}
