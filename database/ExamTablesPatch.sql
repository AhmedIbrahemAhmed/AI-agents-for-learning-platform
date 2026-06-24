-- 1. Switch context to the GraduationProject database
-- (Ensure this database is already created, or run 'CREATE DATABASE GraduationProject;' first)
USE GraduationProject;
GO

CREATE TABLE ExamSessions (
    SessionID INT IDENTITY(1,1) PRIMARY KEY,
    Score DECIMAL(3, 2) NULL 
        CONSTRAINT CHK_Score_Range CHECK (Score >= 0.00 AND Score <= 1.00),
    TotalQuestions INT NULL,
    Passed BIT NULL -- BIT is used for booleans (0 = False, 1 = True)
);

-- 2. Create the Answers Table (Linked to Sessions)
CREATE TABLE SessionAnswers (
    AnswerID INT IDENTITY(1,1) PRIMARY KEY,
    SessionID INT NOT NULL,
    Topic NVARCHAR(100) NOT NULL,
    Question NVARCHAR(MAX) NOT NULL,
    UserAnswer NVARCHAR(MAX) NULL,
    CorrectAnswer NVARCHAR(MAX) NOT NULL,
    IsCorrect BIT NULL,
    Explanation NVARCHAR(MAX) NULL,
    
    -- Relationship: If a session is deleted, delete its answers
    CONSTRAINT FK_SessionAnswers_ExamSessions 
        FOREIGN KEY (SessionID) REFERENCES ExamSessions(SessionID) 
        ON DELETE CASCADE
);

-- 3. Create the Choices Table (Linked to SessionAnswers)
CREATE TABLE AnswerChoices (
    ChoiceID INT IDENTITY(1,1) PRIMARY KEY,
    AnswerID INT NOT NULL,
    ChoiceKey CHAR(1) NOT NULL,       -- Stores 'a', 'b', 'c', etc.
    ChoiceValue NVARCHAR(MAX) NOT NULL, -- Stores 'inheritance', 'polymorphism', etc.
    
    -- Relationship: If an answer is deleted, delete its choices
    CONSTRAINT FK_AnswerChoices_SessionAnswers 
        FOREIGN KEY (AnswerID) REFERENCES SessionAnswers(AnswerID) 
        ON DELETE CASCADE
);