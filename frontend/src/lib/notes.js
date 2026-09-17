// Vendor-safe wording for note CODES. The server and the stripper only ever send
// codes; anything without an entry here is never shown. No figures, ever.
const N = {
  hidden_sheets_removed: () => 'Hidden sheets were left out.',
  sheet_without_a_table_removed: () => 'A sheet with no policy table was left out.',
  personal_details_removed_above_header: () => 'Personal details above the table were removed.',
  cells_removed_from_needed_column: (n) => `${n > 1 ? `${n} cells` : 'A cell'} looked like personal details and ${n > 1 ? 'were' : 'was'} removed.`,
  needed_column_held_names: (n) => `${n > 1 ? `${n} cells` : 'A cell'} looked like names and ${n > 1 ? 'were' : 'was'} removed.`,
  server_removed_personal_details: () => 'A few extra personal details were removed on arrival.',
  rows_without_date_of_birth: (n) => `${n} ${n === 1 ? 'row has' : 'rows have'} no date of birth.`,
  dob_column_not_found: () => 'No date of birth column was found.',
  policy_number_column_not_found: () => 'No policy number column was found.',
  status_column_not_found: () => 'No policy status column was found.',
  expected_columns_missing: (n, ctx) => `This doesn't look like the usual ${ctx.insurerName || 'insurer'} export. We've taken it anyway.`,
};
export function noteText(note, ctx = {}) {
  const f = N[note.code];
  return f ? f(note.count || 1, ctx) : null;
}
export const FAILURES = {
  password_protected: 'This file is password protected. Remove the password and add it again.',
  no_table_found: "We couldn't find a policy table in this file.",
  unreadable_file: "This file couldn't be read. Try exporting it again as Excel or CSV.",
  worker_crashed: 'Something went wrong preparing this file. Add it again.',
  network: 'The connection dropped while sending this file.',
  too_large: 'This file is too large to send.',
};
