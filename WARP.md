# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Development Commands

### Setup & Installation

```bash
# Create and activate virtual environment (Windows PowerShell)
python -m venv venv
venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Initialize database (creates DuckDB by default)
python -c "from database.db_manager import init_database; init_database()"
```

### Running the Bot

```bash
# Run the main application (bot + web admin panel)
python main.py

# Web admin panel runs on port 5000 by default
# Access at: http://localhost:5000
```

### Testing

```bash
# Run tests with pytest
pytest

# Run specific test file
pytest tests/test_antifraud.py

# Run with verbose output
pytest -v
```

### Database Operations

```bash
# Initialize database
python -c "from database.db_manager import init_database; init_database()"

# Clear all data (use with caution)
python -c "from database.db_manager import clear_all_data; clear_all_data()"
```

## Architecture Overview

### Core Components

The application consists of three main subsystems:

1. **Telegram Bot** (`bot/telegram_bot.py`)
   - Handles user interactions via Telegram API
   - Manages registration flow (name → phone → username → photo)
   - Uses state machine pattern via `bot/states.py`
   - Thread pool executor for concurrent registration handling
   - Implements retry logic for polling conflicts

2. **Web Admin Panel** (`web/admin_panel.py`)
   - Flask-based web interface for administrators
   - Session-based authentication with password from `.env`
   - Caching layer for frequent database queries
   - Support ticket management system
   - Export functionality (CSV/Excel)

3. **Database Layer** (`database/db_manager.py`)
   - Supports both DuckDB (default) and SQLite
   - Connection pooling with retry mechanism
   - Transaction management for data consistency
   - Automatic migration handling

### Key Design Patterns

**State Management**: The bot uses a finite state machine to track user progress through registration:
- `WAITING_NAME` → `WAITING_PHONE` → `WAITING_USERNAME` → `WAITING_PHOTO`
- State transitions handled in `bot/telegram_bot.py::handle_text_messages()`

**Anti-Fraud System** (`utils/anti_fraud.py`):
- Risk scoring algorithm with weighted checks
- Photo hash comparison using SHA256 and perceptual hashing
- Rate limiting detection for burst registrations
- Configurable risk thresholds

**Async Processing**: Registration submissions use ThreadPoolExecutor to:
- Immediately respond to users for better UX
- Process database writes and validations in background
- Handle high-load scenarios with concurrent registrations

### Data Flow

1. **User Registration**:
   - User starts with `/start` command
   - Bot guides through 4-step process
   - Photo validation via `utils/image_validation.py`
   - Anti-fraud checks run asynchronously
   - Application saved to database

2. **Admin Operations**:
   - Web panel authenticates admin via session
   - Database queries cached with 5-second TTL
   - Support tickets create bidirectional communication
   - Broadcast messages use rate limiting to avoid Telegram API limits

3. **Winner Selection**:
   - Cryptographic randomization via `utils/randomizer.py`
   - Hash-based seed generation for transparency
   - Winner announcement with verification details

## Configuration

### Environment Variables (.env)

Critical settings that must be configured:

- `BOT_TOKEN`: Telegram bot token from @BotFather
- `ADMIN_PASSWORD`: Web admin panel password
- `ADMIN_IDS`: Comma-separated Telegram user IDs for bot admins
- `DATABASE_TYPE`: 'duckdb' (default) or 'sqlite'
- `WEB_BASE_URL`: Base URL for web panel (auto-detected if not set)
- `BROADCAST_RATE_PER_SEC`: Messages per second for broadcasts (default: 8)

### Database Schema

The main `applications` table includes:
- User data: `name`, `phone_number`, `telegram_username`, `telegram_id`
- Photo data: `photo_path`, `photo_hash`, `photo_phash`
- Anti-fraud: `risk_score`, `risk_level`, `risk_details`, `status`
- Leaflet validation: `leaflet_status`, `stickers_count`, `validation_notes`
- System fields: `timestamp`, `is_winner`, `participant_number`

Support tickets stored in `support_tickets` table with conversation history.

## Common Tasks

### Monitoring & Debugging

```bash
# View application logs
tail -f bot.log

# Check database contents (DuckDB)
python -c "from database.db_manager import get_all_applications; print(len(get_all_applications()))"

# Test Telegram connection
python -c "import telebot; bot = telebot.TeleBot('YOUR_TOKEN'); print(bot.get_me())"
```

### Production Deployment

The repository includes deployment scripts for Linux VPS:
- `deploy.sh`: Automated deployment with dependency installation
- `setup-ip.sh`: Quick IP-based configuration
- `telegram-bot.service`: Systemd service configuration
- `nginx-telegram-bot.conf`: Nginx reverse proxy setup

### Error Recovery

Common issues and solutions:

1. **Polling conflict (409 error)**: Bot implements automatic retry with exponential backoff
2. **Database locked**: Connection pooling with retry mechanism handles concurrent access
3. **Rate limits**: Broadcast system includes configurable delays and retry logic
4. **Photo validation failures**: Manual review flag triggers admin notification

## Important Implementation Details

### Thread Safety
- Database operations use connection pooling
- User state management uses thread-local storage
- Registration processing uses ThreadPoolExecutor with 10 workers

### Performance Optimizations
- Web admin caching reduces database load
- Lazy loading for photo files
- Batch processing for broadcasts
- Quick response to users with background processing

### Security Considerations
- Session-based authentication for web panel
- Environment variables for sensitive configuration
- Photo hash verification prevents duplicates
- Rate limiting on registration attempts

## Module Responsibilities

- `bot/`: Telegram bot logic and keyboards
- `web/`: Flask web application and admin interface  
- `database/`: Database abstraction and migrations
- `utils/`: Shared utilities (anti-fraud, file handling, validation)
- `config.py`: Central configuration management
- `main.py`: Application entry point with thread management