import streamlit as st
from datetime import date
from supabase import create_client, Client
import pandas as pd
import hashlib

# --- Constants ---
CATEGORY_OPTIONS = {
    "Food": ["Breakfast", "Lunch", "Dinner", "Snacks"],
    "Transportation": ["Bus", "Train", "Flight", "Ship", "Bike", "Cabs"],
    "Utilities": ["Electricity", "Internet"],
    "Health": ["Medicine", "Hospital"],
    "Entertainment": ["Theatre", "Concerts", "Subscriptions"],
    "Others": ["Gifts", "Bank Charges", "Insurance"]
}

# --- Initialize Supabase ---
@st.cache_resource(hash_funcs={Client: lambda _: None})
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key) 

supabase = init_supabase()

# --- Helper Functions ---
def fetch_accounts():
    response = supabase.table("accounts").select("id, account_name, balance").execute()
    return response.data or []

def display_account_balances(accounts_data):
    st.title("💳 Spring NS Expense Entry Form")
    st.markdown("## 🏦 Current Account Balances")
    df = pd.DataFrame(accounts_data)
    df = df[["account_name", "balance"]].rename(columns={
        "account_name": "Account", "balance": "Balance (₹)"
    })
    st.dataframe(df, use_container_width=True)

def render_expense_form(accounts_data):
    billing_date = st.date_input("Billing Date", value=date.today())
    payment_date = st.date_input("Payment Date", value=date.today())
    expense_type = st.selectbox("Type", ["Expense"])
    main_category = st.selectbox("Main Category", list(CATEGORY_OPTIONS.keys()))
    subcategory = st.selectbox("Subcategory", CATEGORY_OPTIONS[main_category], key=main_category)
    description = st.text_input("Description", max_chars=100)

    account_options = {
        f"{acct['account_name']} (₹{acct['balance']:.2f})": acct for acct in accounts_data
    }
    selected_label = st.selectbox("From Account", list(account_options.keys()))
    selected_account = account_options[selected_label]

    price = st.number_input("Price", min_value=0.0, step=0.1)
    quantity = st.number_input("Quantity", min_value=1, step=1, value=1)
    amount = round(price * quantity, 2)
    st.markdown(f"**Calculated Amount:** ₹{amount:.2f}")

    return {
        "billing_date": billing_date,
        "payment_date": payment_date,
        "expense_type": expense_type,
        "main_category": main_category,
        "subcategory": subcategory,
        "description": description,
        "selected_account": selected_account,
        "price": price,
        "quantity": quantity,
        "amount": amount
    }

def log_cash_flow(txn_id, txn_type, account, amount, balance, billing_date, payment_date):
    try:
        record = {
            "transaction_id": txn_id,
            "type": txn_type,
            "account": account,
            "transaction_amount": amount,
            "updated_balance": balance,
            "billing_date": billing_date,
            "payment_date": payment_date
        }
        result = supabase.table("cash_flow").insert(record).execute()
        return None if result.data else "⚠️ Failed to log cash flow."
    except Exception as e:
        return f"Error logging cash flow: {e}"

def submit_expense(form_data):
    errors = []
    if not form_data["description"]:
        errors.append("⚠️ Description is required.")
    elif form_data["price"] <= 0:
        errors.append("⚠️ Price must be greater than 0.")
    elif form_data["quantity"] <= 0:
        errors.append("⚠️ Quantity must be at least 1.")
    else:
        try:
            billing_date_str = form_data["billing_date"].isoformat()
            count_resp = supabase.table("expenses").select("id", count="exact").eq("billing_date", billing_date_str).execute()
            count = count_resp.count or 0
            txn_id = f"EXP-{form_data['billing_date'].strftime('%Y%m%d')}-{count + 1:03d}"
            new_balance = float(form_data["selected_account"]["balance"]) - form_data["amount"]

            if new_balance < 0:
                errors.append("❌ Insufficient balance in the selected account.")
            else:
                update_resp = supabase.table("accounts").update({"balance": new_balance}).eq("id", form_data["selected_account"]["id"]).execute()
                if update_resp.data is None:
                    errors.append("❌ Failed to update account balance.")
                else:
                    expense_record = {
                        "id": txn_id,
                        "billing_date": billing_date_str,
                        "payment_date": form_data["payment_date"].isoformat(),
                        "type": form_data["expense_type"],
                        "main_category": form_data["main_category"],
                        "subcategory": form_data["subcategory"],
                        "description": form_data["description"],
                        "from_account": form_data["selected_account"]["account_name"],
                        "price": form_data["price"],
                        "quantity": form_data["quantity"],
                        "amount": form_data["amount"]
                    }
                    insert_resp = supabase.table("expenses").insert(expense_record).execute()
                    if insert_resp.data is None:
                        errors.append("❌ Insert failed.")
                    else:
                        err = log_cash_flow(
                            txn_id, "Debit", form_data["selected_account"]["account_name"],
                            form_data["amount"], new_balance,
                            form_data["billing_date"].isoformat(),
                            form_data["payment_date"].isoformat()
                        )
                        if err:
                            errors.append(err)
                        else:
                            st.session_state.submitted = True
                            st.success(f"✅ Expense saved! Entry ID: `{txn_id}`")
                            st.rerun()
        except Exception as e:
            errors.append(f"Unexpected error occurred: {e}")
    return errors

def show_latest_transactions():
    st.markdown("### 🧾 Latest 10 Transactions")
    try:
        response = supabase.table("expenses").select("*").order("id", desc=True).limit(10).execute()
        if response.data:
            df = pd.DataFrame(response.data)
            df = df[["id", "billing_date", "payment_date", "main_category", "subcategory", "description", "from_account", "amount"]]
            df["billing_date"] = pd.to_datetime(df["billing_date"]).dt.strftime("%b %d, %Y")
            df["payment_date"] = pd.to_datetime(df["payment_date"]).dt.strftime("%b %d, %Y")
            df = df.sort_values(by="id", ascending=False)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No transactions found.")
    except Exception as e:
        st.error(f"Error fetching transactions: {e}")

def undo_transactions():
    st.markdown("### 🔄 Undo Any Transaction")
    search_term = st.text_input("🔍 Search transactions by description or account")
    try:
        response = supabase.table("expenses").select("*").order("id", desc=True).limit(20).execute()
        if response.data:
            df = pd.DataFrame(response.data)
            if search_term:
                df = df[df["description"].str.contains(search_term, case=False) | df["from_account"].str.contains(search_term, case=False)]

            for _, row in df.iterrows():
                txn_id = row["id"]
                amount = float(row["amount"])
                acc_name = row["from_account"]
                billing = pd.to_datetime(row["billing_date"]).strftime("%b %d, %Y")
                desc = row["description"]

                with st.expander(f"🧾 {txn_id} | ₹{amount:.2f} | {acc_name} | {billing}"):
                    st.markdown(f"_Description: {desc}_")
                    if st.button(f"Confirm Undo for `{txn_id}`", key=f"undo_{txn_id}"):
                        try:
                            supabase.table("expenses").delete().eq("id", txn_id).execute()
                            supabase.table("cash_flow").delete().eq("transaction_id", txn_id).execute()

                            acc_lookup = supabase.table("accounts").select("id, balance").eq("account_name", acc_name).execute()
                            if not acc_lookup.data:
                                st.error(f"Account {acc_name} not found.")
                            else:
                                acc = acc_lookup.data[0]
                                new_balance = float(acc["balance"]) + amount
                                supabase.table("accounts").update({"balance": new_balance}).eq("id", acc["id"]).execute()
                                st.success(f"✅ Undone transaction `{txn_id}` and restored ₹{amount:.2f} to {acc_name}.")
                                st.rerun()
                        except Exception as e:
                            st.error(f"Undo failed: {e}")
        else:
            st.info("No transactions found.")
    except Exception as e:
        st.error(f"Error loading undo transactions: {e}")

# --- Sample user database with hashed passwords ---
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

USER_CREDENTIALS = {
    "nadin": hash_password("Nadin@2005"),  # Change this to your desired password
    "admin": hash_password("adminpass")
}

def login():
    st.title("🔐 Login to Spring NS Tracker")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    login_btn = st.button("Login")

    if login_btn:
        if username in USER_CREDENTIALS and USER_CREDENTIALS[username] == hash_password(password):
            st.success("✅ Login successful!")
            st.session_state["logged_in"] = True
            st.session_state["username"] = username

            st.success("Login successful! Please manually refresh or press [R] to reload the app.")
            st.stop()
        else:
            st.error("❌ Invalid username or password")



# --- Main App ---
def main():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False

    if not st.session_state.logged_in:
        login()
        return

    st.sidebar.success(f"👋 Welcome, {st.session_state['username']}")
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.experimental_rerun()

    accounts_data = fetch_accounts()
    if not accounts_data:
        st.error("❌ No accounts found. Please set up accounts in the database.")
        return

    display_account_balances(accounts_data)
    form_data = render_expense_form(accounts_data)

    if form_data["amount"] > form_data["selected_account"]["balance"]:
        st.warning("⚠️ This transaction may exceed the selected account's balance.")

    if "submitted" not in st.session_state:
        st.session_state.submitted = False

    if st.button("Submit", disabled=st.session_state.submitted):
        errors = submit_expense(form_data)
        if errors:
            with st.expander("❗ Errors occurred"):
                for msg in errors:
                    st.markdown(f"- {msg}")

    show_latest_transactions()
    undo_transactions()


if __name__ == "__main__":
    main()
