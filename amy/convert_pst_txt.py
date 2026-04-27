import pypff  # The import remains pypff even if the install is libpff-python
import os

def extract_pst(pst_file_path, output_folder):
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    pst = pypff.file()
    pst.open(pst_file_path)
    root = pst.get_root_folder()

    def parse_folder(folder):
        for sub_folder in folder.sub_folders:
            parse_folder(sub_folder)
        
        # Only extract from "Sent Items" to get your writing style
        if folder.name and "Sent Items" in folder.name:
            num_messages = folder.number_of_sub_messages
            print(f"Extracting from: {folder.name} (Found {num_messages} messages)")
            
            for message in folder.sub_messages:
                try:
                    date = message.client_submit_time.strftime("%Y-%m-%d") if message.client_submit_time else "Unknown"
                except Exception:
                    date = "Unknown"
                
                try:
                    raw_subj = message.subject
                    subject = "".join(x for x in str(raw_subj) if x.isalnum() or x in "._- ") if raw_subj else "NoSubject"
                except Exception:
                    subject = "ErrorSubject"

                filename = f"{date}_{subject[:30]}.txt"
                
                try:
                    body = message.plain_text_body
                    if not body:
                        # Fallback to HTML body if plain text is empty
                        body = message.html_body
                        
                    if body:
                        # Decode safely
                        decoded_body = body.decode('utf-8', errors='ignore')
                        with open(os.path.join(output_folder, filename), "w", encoding="utf-8") as f:
                            f.write(decoded_body)
                    else:
                        print(f"Skipped {filename}: No text or HTML body found.")
                except Exception as e:
                    print(f"Error processing {filename}: {e}")
                    continue

    parse_folder(root)
    pst.close()

# RUN IT
extract_pst("C:\\EmailExport\\amy@welink.com.au.pst", "hist_email")