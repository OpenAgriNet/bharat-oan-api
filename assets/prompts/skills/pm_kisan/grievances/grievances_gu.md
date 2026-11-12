---
summary: નવી PM-KISAN ફરિયાદ નોંધાવવાની સેવા હાલમાં ઉપલબ્ધ નથી. ફરિયાદ સ્થિતિ
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| ફરિયાદ નોંધણી | — | — | નવી PM-KISAN ફરિયાદ નોંધાવવાની સેવા હાલમાં ઉપલબ્ધ નથી. |
| ફરિયાદ સ્થિતિ | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **સ્રોત: PM-KISAN ફરિયાદ પોર્ટલ** | OTP-પ્રથમ પ્રવાહ. જરૂરી: PM-KISAN નોંધણી નંબર અને OTP |

**નવી PM-KISAN ફરિયાદ નોંધાવવાની સેવા હાલમાં ઉપલબ્ધ નથી.**
ખેડૂતને આ જણાવીને અટકો. ફરિયાદ નોંધાવવા વિગતો, નોંધણી નંબર કે OTP ન માંગો; OTP ન મોકલો, ફરિયાદ ન નોંધાવો અને આપમેળે બીજી ફરિયાદ સેવા તરફ ન મોકલો. અગાઉ નોંધાવેલી ફરિયાદની સ્થિતિ તપાસી શકાય છે.

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
