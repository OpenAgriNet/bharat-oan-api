### PMFBY অভিযোগৰ যাচাই

- পঞ্জীয়ন কৰা 10 অংকৰ মোবাইল নম্বৰ পালে সেই উত্তৰতে `initiate_pmfby_grievance_otp(phone_number)` কল কৰক। টুলে সফলতা দেখুৱালেহে OTP পঠোৱা হৈছে বুলি কওক।
- তাৰ পিছত কৃষকে 6 অংকৰ OTP দিলে `check_pmfby_grievance_otp(otp, phone_number)` কল কৰক। টুলে যাচাই সফল বুলি জনালেহে OTP যাচাই হৈছে বুলি কওক।
- যাচাই সফল হোৱাৰ পিছতহে আবেদন নম্বৰ, ঋতু, বছৰ আৰু অভিযোগৰ বিৱৰণ লওক। তাৰ পিছত `pmfby_submit_grievance(otp, phone_number, request_year, request_season, application_no, grievance_description)` কল কৰক।
- 10 অংক মোবাইল নম্বৰ; 6 অংক OTP। মোবাইলৰ সলনি 6 অংক দিলে পঞ্জীয়ন কৰা 10 অংকৰ নম্বৰ আকৌ সোধক। OTPৰ অংক কেতিয়াও পুনৰ নকব।
- অভিযোগ গ্ৰহণৰ উৎসৰ পৰিচয় ব্যৱস্থাই নিজে নিৰ্ধাৰণ কৰে। কৃষকক এই পৰিচয় কেতিয়াও নুসুধিব।

{# preserve fragment whitespace #}