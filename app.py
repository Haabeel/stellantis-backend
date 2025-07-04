from datetime import datetime
from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
from core.job_card_creator import create_job_from_ui_input
from recommender_new import recommend_engineers_memory_cf
from job_manager import get_connection, get_task_ids_for_job, update_task_assignment 
import sqlite3
from job_manager import (
    fetch_all_jobs,
    fetch_unassigned_jobs,
    fetch_available_engineers,
    check_availability,
    # update_job_assignment,
    mark_engineer_unavailable,
    mark_engineer_available,
    get_task_id_for_job,
    complete_job,
)


app = Flask(__name__)
CORS(app)
# def _build_cors_preflight_response():
#     response = make_response()
#     response.headers.add("Access-Control-Allow-Origin", "*")
#     response.headers.add('Access-Control-Allow-Headers', "*")
#     response.headers.add('Access-Control-Allow-Methods', "*")
#     return response

DB_PATH = "database/workshopA.db"
# def get_connection():
#     return sqlite3.connect(DB_PATH, timeout=10)


@app.route('/engineers', methods=['GET'])
def get_all_engineers():
    """
    Fetches all engineer profiles from the database and returns them as JSON.
    """
    try:
        # Use a context manager for safe connection handling
        with get_connection() as conn:
            # Set the row_factory to sqlite3.Row to get dictionary-like results
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM engineer_profiles')
            
            # fetchall() will now return a list of sqlite3.Row objects
            rows = cursor.fetchall()
            
            # Convert the list of Row objects into a list of dictionaries
            # This is a clean and standard way to create JSON-serializable data
            engineers = [dict(row) for row in rows]
            
        return jsonify(engineers), 200
    except Exception as e:
        # Return a generic server error if anything goes wrong
        print(f"Error fetching engineers: {e}") # Log the error for debugging
        return jsonify({'error': 'An internal server error occurred'}), 500

@app.route('/engineers/<string:engineer_id>', methods=['GET'])
def get_engineer_profile(engineer_id):
    """
    Fetches the profile data for a single engineer.
    """
    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM engineer_profiles WHERE Engineer_ID = ?', (engineer_id,))
            engineer = cursor.fetchone()
            
            if engineer:
                return jsonify(dict(engineer)), 200
            else:
                return jsonify({'error': 'Engineer not found'}), 404
    except Exception as e:
        print(f"Error fetching engineer profile {engineer_id}: {e}")
        return jsonify({'error': 'An internal server error occurred'}), 500

@app.route('/engineers/<string:engineer_id>/details', methods=['GET'])
def get_engineer_details(engineer_id):
    """
    Fetches all details for a specific engineer, including their full profile
    and a complete list of all their tasks (ongoing and historical).
    This is achieved with a single, efficient SQL query.
    """
    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # First, check if the engineer exists to provide a clean 404 error
            cursor.execute('SELECT 1 FROM engineer_profiles WHERE Engineer_ID = ?', (engineer_id,))
            if not cursor.fetchone():
                return jsonify({'error': 'Engineer not found'}), 404

            # This powerful query uses a Common Table Expression (CTE) to unify tasks
            # from two tables and then joins the engineer's profile to each task.
            sql = """
                WITH all_tasks AS (
                    -- Select from active jobs in job_card
                    SELECT 
                        Job_Id, 
                        Task_Id, 
                        Task_Description, 
                        Status, 
                        Estimated_Standard_Time, 
                        Time_Started, 
                        'ongoing' as source, 
                        NULL as Outcome_Score, 
                        NULL as Time_Taken_minutes,
                        Engineer_Id,
                        VIN,
                        Make,
                        Model
                    FROM job_card
                    WHERE Engineer_Id = ?
                    
                    UNION ALL
                    
                    -- Select from completed jobs in job_history, aliasing columns to match
                    SELECT 
                        Job_ID AS Job_Id, 
                        Task_Id, 
                        Task_Description, 
                        Status, 
                        Estimated_Standard_Time, 
                        Time_Started, 
                        'history' as source, 
                        Outcome_Score, 
                        Time_Taken_minutes,
                        Engineer_Id,
                        VIN,
                        Make,
                        Model
                    FROM job_history
                    WHERE Engineer_Id = ?
                )
                -- LEFT JOIN the unified tasks with the engineer's full profile
                SELECT
                    t.*,        -- Selects all columns from the unified tasks (t)
                    p.*         -- Selects all columns from the engineer's profile (p)
                FROM all_tasks t
                LEFT JOIN engineer_profiles p ON t.Engineer_Id = p.Engineer_ID;
            """
            
            # The engineer_id is used twice, once for each part of the UNION
            cursor.execute(sql, (engineer_id, engineer_id))
            
            # The result is a list of tasks, each enriched with the full engineer profile
            enriched_tasks = [dict(row) for row in cursor.fetchall()]
            
        return jsonify(enriched_tasks), 200
        
    except sqlite3.Error as e:
        print(f"Database error for engineer {engineer_id}: {e}")
        return jsonify({'error': 'A database error occurred'}), 500
    except Exception as e:
        print(f"Error fetching details for engineer {engineer_id}: {e}")
        return jsonify({'error': 'An internal server error occurred'}), 500

@app.route('/timeline', methods=['GET'])
def get_timeline_data():
    """
    Fetches all tasks that were active on a specific date.
    'active' means tasks that were started, in progress, or completed on that day.
    """
    # Get the date from query parameters, default to today if not provided
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    
    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # This query unifies tasks from both tables that were active on the target date
            sql = """
                SELECT 
                    Job_Id, Task_Id, Task_Description, Status, Estimated_Standard_Time, 
                    Time_Started, Engineer_Id, Engineer_Name, NULL as Time_Taken_minutes
                FROM job_card
                WHERE DATE(Time_Started) = ? OR (Status IN ('Pending', 'Assigned') AND DATE(Date_Created) <= ?)

                UNION ALL

                SELECT 
                    Job_ID as Job_Id, Task_Id, Task_Description, Status, Estimated_Standard_Time, 
                    Time_Started, Engineer_Id, Engineer_Name, Time_Taken_minutes
                FROM job_history
                WHERE DATE(Time_Started) = ?
            """
            
            cursor.execute(sql, (date_str, date_str, date_str))
            tasks = [dict(row) for row in cursor.fetchall()]
            
        return jsonify(tasks), 200
    except Exception as e:
        print(f"Error fetching timeline data for date {date_str}: {e}")
        return jsonify({'error': 'An internal server error occurred'}), 500

@app.route("/create-job", methods=["POST"])
def create_job_endpoint():
    data = request.get_json()

    # Extract the data sent from the frontend
    job_name = data.get('jobName')
    vin = data.get('vin')
    make = data.get('make')
    model = data.get('model')
    mileage = data.get('mileage')
    urgency = data.get('urgency')
    selected_tasks = data.get('selectedTasks') # This will be the list of Task IDs

    # Basic validation
    if not all([job_name, vin, make, model, mileage, urgency]):
        return jsonify({'error': 'Missing required fields'}), 400

    # Call your business logic function
    success, message, generated_job_id = create_job_from_ui_input(
        job_name=job_name,
        vin=vin, make=make, model=model, mileage=mileage,
        urgency=urgency,
        selected_tasks=selected_tasks
    )

    if success:
        # --- INCLUDE THE GENERATED JOB_ID IN THE RESPONSE ---
        return jsonify({'message': message, 'job_id': generated_job_id}), 201
    else:
        return jsonify({'error': message}), 500


# @app.route("/create-job", methods=["POST"]) # Removed "OPTIONS" here, Flask-Cors handles it
# def create_job():
#     job_cards = request.get_json()
#     if not isinstance(job_cards, list):
#         return jsonify({'error': 'Input should be a list of job cards'}), 400

#     conn = None # Initialize conn to None
#     try:
#         conn = get_connection() # Use the single, correct function
#         cursor = conn.cursor()

#         # Iterate and execute inserts
#         for job in job_cards:
#             cursor.execute("""
#                 INSERT INTO job_card_table (
#                     Assigned_Engineer_Id, Date_Created, Estimated_Standard_Time, Job_Card_ID,
#                     Job_Name, Make, Mileage, Model, Outcome_Score, Status, Task_Description,
#                     Task_ID, Time_Ended, Time_Started, Urgency, VIN
#                 ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
#             """, (
#                 job.get('Assigned_Engineer_Id'),
#                 job.get('Date_Created'),
#                 job.get('Estimated_Standard_Time'),
#                 job.get('Job_Card_ID'), # Assuming these are now integers from frontend
#                 job.get('Job_Name'),
#                 job.get('Make'),
#                 job.get('Mileage'),
#                 job.get('Model'),
#                 job.get('Outcome_Score'),
#                 job.get('Status'),
#                 job.get('Task_Description'),
#                 job.get('Task_ID'), # Assuming these are now integers from frontend
#                 job.get('Time_Ended'),
#                 job.get('Time_Started'),
#                 job.get('Urgency'),
#                 job.get('VIN')
#             ))
#         conn.commit() # Commit all changes if loop completes without error
#         return jsonify({'message': 'Job cards added successfully'}), 201

#     except sqlite3.IntegrityError as e:
#         # Catch specific integrity errors (e.g., UNIQUE constraint failure)
#         if conn:
#             conn.rollback() # Rollback changes if an integrity error occurs
#         print(f"Integrity error: {e}")
#         return jsonify({'error': 'Database integrity constraint violated.', 'details': str(e)}), 409 # 409 Conflict

#     except sqlite3.Error as e:
#         # Catch any other SQLite errors
#         if conn:
#             conn.rollback() # Rollback changes if any other error occurs
#         print(f"Database error: {e}")
#         return jsonify({'error': 'A database error occurred', 'details': str(e)}), 500

#     except Exception as e:
#         # Catch any other unexpected Python errors
#         if conn:
#             conn.rollback()
#         print(f"An unexpected error occurred: {e}")
#         return jsonify({'error': 'An unexpected server error occurred.', 'details': str(e)}), 500
        
#     finally:
#         # This block ensures the connection is ALWAYS closed, regardless of success or failure.
#         if conn:
#             conn.close()

@app.route("/jobs", methods=["GET"])
def get_jobs():
    jobs_df = fetch_all_jobs()
    jobs = jobs_df.to_dict(orient="records")
    return jsonify(jobs)


@app.route("/jobs/unassigned", methods=["GET"])
def get_unassigned_jobs():
    jobs_df = fetch_unassigned_jobs()
    jobs = jobs_df.to_dict(orient="records")
    return jsonify(jobs)

@app.route("/engineers/available", methods=["GET"])
def get_available_engineers():
    eng_df = fetch_available_engineers()
    engineers = eng_df.to_dict(orient="records")
    return jsonify(engineers)

# In app.py

@app.route("/jobs/assign-all-tasks", methods=["POST"]) # Changed route name for clarity
def assign_engineer_to_all_tasks_for_job(): # Changed function name for clarity
    data = request.get_json()
    job_card_id = data.get("job_card_id")

    if not job_card_id:
        return jsonify({"error": "Missing job_card_id"}), 400

    # Get ALL Task_IDs for this Job_Card_ID
    task_ids = get_task_ids_for_job(job_card_id) # Use the new plural function
    if not task_ids:
        return jsonify({"error": f"No tasks found for Job_Card_ID {job_card_id}"}), 404

    assignment_results = []
    
    # Loop through each individual task
    for task_id in task_ids:
        engineer_assigned = None
        recommendation_reason = "No recommendation provided"
        status = "Failed: No available engineer"

        try:
            recommendations, reason = recommend_engineers_memory_cf(task_id, top_n=5)
            recommendation_reason = reason # Store the reason for this task

            if isinstance(recommendations, str):
                status = f"Failed: Recommendation system error - {recommendations}"
            else:
                for eng_id, score in recommendations:
                    if check_availability(eng_id):
                        engineer_assigned = eng_id
                        break

                if engineer_assigned:
                    update_task_assignment(task_id, engineer_assigned) # Use new update_task_assignment
                    mark_engineer_unavailable(engineer_assigned)
                    status = "Assigned"
                else:
                    status = "Failed: No available engineer found in recommendations"

        except Exception as e:
            status = f"Failed: Unexpected error during assignment - {str(e)}"
            print(f"Error assigning engineer to task {task_id}: {e}") # Log error on backend

        assignment_results.append({
            "task_id": task_id,
            "engineer_assigned": engineer_assigned,
            "status": status,
            "recommendation_reason": recommendation_reason
        })

    # Return a summary of all assignments
    return jsonify(
        {
            "message": f"Assignment process completed for Job {job_card_id}",
            "assignments": assignment_results
        }
    ), 200

@app.route("/jobs/assign", methods=["POST"])
def assign_engineer_to_job():
    data = request.get_json()
    job_card_id = data.get("job_card_id")

    if not job_card_id:
        return jsonify({"error": "Missing job_card_id"}), 400

    task_id = get_task_id_for_job(job_card_id)
    if not task_id:
        return jsonify({"error": "Job_Card_ID not found"}), 404

    recommendations, reason = recommend_engineers_memory_cf(task_id, top_n=5)
    if isinstance(recommendations, str):
        return jsonify({"error": recommendations}), 400

    assigned_engineer = None
    for eng_id, score in recommendations:
        if check_availability(eng_id):
            assigned_engineer = eng_id
            break

    if not assigned_engineer:
        return jsonify({"error": "No available engineer found"}), 409

    update_job_assignment(job_card_id, assigned_engineer)
    mark_engineer_unavailable(assigned_engineer)

    return jsonify(
        {
            "message": f"Engineer {assigned_engineer} assigned to job {job_card_id}",
            "recommendation_reason": reason,
        }
    ), 200


@app.route("/jobs/complete", methods=["POST"])
def complete_job_endpoint():
    data = request.get_json()
    job_card_id = data.get("job_card_id")
    outcome_score = data.get("outcome_score")

    if not job_card_id or outcome_score is None:
        return jsonify({"error": "Missing job_card_id or outcome_score"}), 400

    try:
        complete_job(job_card_id, outcome_score)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify(
        {"message": f"Job {job_card_id} marked as completed with score {outcome_score}"}
    ), 200

@app.route('/jobs/start-task', methods=['POST'])
def start_task():
    """
    Starts a task by updating its status to 'In Progress' and setting the start time.
    """
    data = request.get_json()
    job_id = data.get('job_id')
    task_id = data.get('task_id')

    if not job_id or not task_id:
        return jsonify({'error': 'Missing job_id or task_id'}), 400

    with get_connection() as conn:
        conn.row_factory = sqlite3.Row  # Allows accessing columns by name
        cursor = conn.cursor()

        try:
            # First, check the task's current status
            cursor.execute("SELECT Status FROM job_card WHERE Job_Id = ? AND Task_Id = ?", (job_id, task_id))
            task_record = cursor.fetchone()

            if not task_record:
                return jsonify({'error': 'Task not found'}), 404
            
            # A task can only be started if it's 'Assigned' (not 'Pending')
            if task_record['Status'] != 'Assigned':
                return jsonify({'error': f"Task cannot be started. Status is currently '{task_record['Status']}'"}), 409

            # Set the start time and update the status
            time_started = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute("""
                UPDATE job_card 
                SET Status = 'In Progress', Time_Started = ?
                WHERE Job_Id = ? AND Task_Id = ?
            """, (time_started, job_id, task_id))
            conn.commit()

            return jsonify({'message': f'Task {task_id} started successfully'}), 200

        except sqlite3.Error as e:
            conn.rollback()
            return jsonify({'error': str(e)}), 500

@app.route('/jobs/mark-complete', methods=['POST'])
def mark_task_complete():
    """
    Moves a completed task from the job_card table to the job_history table.
    """
    data = request.get_json()
    job_id = data.get('job_id')
    task_id = data.get('task_id')
    outcome_score = data.get('outcome_score')

    if not job_id or not task_id or outcome_score is None:
        return jsonify({'error': 'Missing job_id, task_id, or outcome_score'}), 400

    # This single 'with' block manages the entire transaction
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        try:
            # Fetch the full record from job_card
            cursor.execute("SELECT * FROM job_card WHERE Job_Id = ? AND Task_Id = ?", (job_id, task_id))
            record = cursor.fetchone()

            if not record:
                return jsonify({'error': 'Task not found in active jobs'}), 404

            if not record['Time_Started']:
                return jsonify({'error': 'Cannot complete a task that has not been started'}), 400

            # --- Calculate Times ---
            time_ended_dt = datetime.now()
            time_started_dt = datetime.strptime(record['Time_Started'], '%Y-%m-%d %H:%M:%S')
            time_taken = int((time_ended_dt - time_started_dt).total_seconds() / 60)

            cursor.execute("""
                INSERT INTO job_history (
                    Job_ID, Job_Name, Task_Id, Task_Description, Status, Date_Completed, Urgency, VIN, Make, Model, Mileage, 
                    Engineer_Id, Engineer_Name, Engineer_Level, Time_Started, Time_Ended, Time_Taken_Minutes, Estimated_Standard_Time, 
                    Outcome_Score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record['Job_Id'], record['Job_Name'], record['Task_Id'], record['Task_Description'], 'Completed',
                time_ended_dt.strftime('%Y-%m-%d %H:%M:%S'), record['Urgency'], record['VIN'], record['Make'],
                record['Model'], record['Mileage'], record['Engineer_Id'], record['Engineer_Name'],
                record['Engineer_Level'], record['Time_Started'], time_ended_dt.strftime('%Y-%m-%d %H:%M:%S'),
                time_taken, record['Estimated_Standard_Time'], outcome_score
            ))

            # --- Delete from job_card ---
            cursor.execute("DELETE FROM job_card WHERE Job_Id = ? AND Task_Id = ?", (job_id, task_id))

            # --- Make the engineer available again USING THE SAME CONNECTION ---
            if record['Engineer_Id']:
                # Pass the existing 'conn' object to the helper function
                mark_engineer_available(conn, record['Engineer_Id'])

            # Commit the entire transaction at the very end
            conn.commit()
            return jsonify({'message': 'Task marked as complete and moved to history'}), 200

        except sqlite3.Error as e:
            conn.rollback() # If anything fails, roll back all changes
            return jsonify({'error': str(e)}), 500


@app.route("/job-history", methods=["GET"])
def get_job_history():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM job_history")
        rows = cursor.fetchall()
        job_history = [
            {
                "job_id": row[0],
                "job_name": row[1],
                "task_id": row[2],
                "task_description": row[3],
                "status": row[4],
                "date_completed": row[5],
                "urgency": row[6],
                "VIN"  : row[7],
                "make": row[8],
                "model": row[9],
                "mileage": row[10],
                "assigned_engineer_id": row[11],
                "engineer_name": row[12],
                "engineer_level": row[13],
                "time_started": row[14],
                "time_ended": row[15],
                "time_taken": row[16],
                "estimated_standard_time": row[17],
                "outcome_score": row[18],
            }
            for row in rows
        ]
        return jsonify(job_history), 200

    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500

    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    app.run(debug=True, port = 4001, use_reloader=False, threaded=False)
