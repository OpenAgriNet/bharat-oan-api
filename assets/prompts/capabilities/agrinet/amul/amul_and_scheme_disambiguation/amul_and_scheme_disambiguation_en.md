### AmulVistaar union schemes — cross-network (use `call_amul_vistaar_network`)

Use this tool when the farmer asks about Amul union schemes or Amul union benefits, including cattle insurance, subsidies, welfare support, or other union-specific scheme names.

Supported union filters:
- `banas`
- `kutch`
- `sumul`
- `surendranagar`

Call `call_amul_vistaar_network` with a short English `query` when possible. Add `union` if the farmer names one of the supported unions. Add `provider_id` only when a canonical ID such as `banas-union` is already known. For clear Amul union scheme queries, do **not** use `search_schemes` or `search_documents`.

**Bare "drip irrigation" (no scheme named):** Drip irrigation is covered by three different schemes — `pdmc` (national, `search_schemes`), `ndksp-drip-irrigation` (Maharashtra, cross-network), and `aif` (Agriculture Infrastructure Fund, cross-network). If the farmer just says "drip irrigation" without naming a scheme/state, ask which one they mean (national PDMC scheme, Maharashtra's Nanaji Deshmukh/NDKSP scheme, or AIF) before calling any tool — never guess or default to `search_documents`.

---


